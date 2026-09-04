# -*- coding: utf-8 -*-
"""
durable intent layer (issue #2) — 让 x402 客户端「一次逻辑支付 = 最多一次外部
执行 + 最终恰好一次本地 booking」跨 ack 丢失/timeout/crash/restart 成立。

设计文档: docs/durable-intent-design.md (§5 = 方案 A: booking ledger 与
consumed 同处一个 durable commit; consumed 由 booking 行存在性推导)。

v1 边界 (Felix 2026-09-04 确认):
  - durable exactly-once 契约只覆盖 intent 路径 (x402 client protect/confirm 未来接入)
  - 非 intent 路径的 guard.book() 保持内存语义, 不宣传为 restart-durable
  - 单逻辑 estate / 单写者部署; 多进程共享预算 = 明确 out of scope

状态机:
    RESERVED ──┬─ mark_in_flight() ──► IN_FLIGHT ──┬─ mark_succeeded() ──► SUCCEEDED ──(consume_once)──► [booked in ledger]
               └─ cancel_reserved() ─► RECONCILED_FAILED (active=0, 可重试)
                                                 IN_FLIGHT └─ mark_unknown() ──► UNKNOWN ──reconcile(proof)──► SUCCEEDED
                                                                                                             └──► RECONCILED_FAILED

active=0 的唯一两条合法路径 (review fix #2):
  1. reconcile(outcome=RECONCILED_FAILED, proof=...) — proof 证明 prior external execution 未发生
  2. cancel_reserved() from RESERVED — durable state 自证从未 dispatch
  (UNKNOWN 绝不能因 timeout 久了就随便释放 scope — UNKNOWN ≠ retry allowed)

方法 (Felix API 草案 + 测试契约, 见 tests/test_intent_exactly_once.py):
    create_or_get(agent, to, idem_key, amount, fingerprint, currency="USD")
    reserve(id) / mark_in_flight(id) / mark_succeeded(id) / mark_unknown(id)
    cancel_reserved(id) / reconcile(id, outcome, proof)
    consume_once(id) / replay_succeeded() / get(id)

语义约定:
  - False = 良性结果 (claim 输了 / 幂等 no-op), 调用方走 reconciliation
  - raise InvalidTransition = 逻辑错误 / 试图绕过 invariant (dispatch boundary 不可模糊)
  - fingerprint 契约: **fingerprint 是 already-canonical request identity, 不是任意
    request JSON** — 调用方必须用归一化原语构造 (amount 先 float()), store 按 canonical
    JSON 字节精确比较 (10 vs 10.0 序列化不同 → 视为不同请求)
  - create_or_get() = 建立 durable reservation (idempotency scope 从此被占)
  - reserve() = pre-dispatch eligibility check (不是 atomic reservation op)
  - consume_once: 只有 intent_id 已有 booking 的 IntegrityError 才算 already-consumed → False;
    其它 integrity 故障 (booking_id 碰撞/FK/schema) → raise, 绝不伪装成已消费
"""
from __future__ import annotations

import json
import math
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

# ── 状态常量 ──────────────────────────────────────────────────────────
RESERVED = "RESERVED"
IN_FLIGHT = "IN_FLIGHT"
SUCCEEDED = "SUCCEEDED"
UNKNOWN = "UNKNOWN"
RECONCILED_FAILED = "RECONCILED_FAILED"

TERMINAL_FAILED = RECONCILED_FAILED  # 终态失败: 释放 scope, 同 key 可重试

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intents (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  agent         TEXT NOT NULL,
  recipient     TEXT NOT NULL,              -- to / resource
  idem_key      TEXT NOT NULL,
  amount        REAL NOT NULL,
  currency      TEXT NOT NULL DEFAULT 'USD',
  fingerprint   TEXT NOT NULL,              -- canonical JSON
  provider_key  TEXT NOT NULL UNIQUE,
  status        TEXT NOT NULL,
  proof         TEXT,
  active        INTEGER NOT NULL DEFAULT 1, -- 0 = RECONCILED_FAILED (proven/cancelled), 释放 scope
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_intents_active_scope
  ON intents (agent, recipient, idem_key) WHERE active = 1;
CREATE TABLE IF NOT EXISTS bookings (
  booking_id    TEXT NOT NULL UNIQUE,       -- durable accounting fact id
  intent_id     INTEGER PRIMARY KEY REFERENCES intents(id),  -- exactly-once 身份
  agent         TEXT NOT NULL,
  recipient     TEXT NOT NULL,
  amount        REAL NOT NULL,
  currency      TEXT NOT NULL DEFAULT 'USD',
  created_at    TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canon_fp(fingerprint: Any) -> str:
    """指纹规范化: 同一逻辑请求必须生成同一字符串 (键序无关, Unicode 不转义)。

    注意: 数值类型不做等价 (10 → "10" vs 10.0 → "10.0") — 这是刻意的字符串精确比较,
    契约要求调用方用归一化原语构造 fingerprint (already-canonical request identity)。
    """
    return json.dumps(fingerprint, sort_keys=True, ensure_ascii=False)


class IntentConflict(Exception):
    """同 scope 同 key、但 fingerprint 不同 → 复用 key 打不同请求 = 冲突。"""


class InvalidTransition(Exception):
    """非法状态迁移 (试图绕过 invariant)。"""


class SqliteIntentStore:
    """SQLite reference backend (stdlib)。进程内 RLock 串行 + BEGIN IMMEDIATE 跨进程安全。"""

    def __init__(self, path: str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, isolation_level=None,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._conn:  # DEFERRED txn 建表即可
            self._conn.executescript(_SCHEMA)

    # ── 内部: 写事务 (BEGIN IMMEDIATE) ────────────────────────────────
    def _write(self, fn):
        """fn(conn) -> value; 单写事务 (BEGIN IMMEDIATE), 失败回滚。"""
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                out = fn(conn)
                conn.commit()
                return out
            except BaseException:
                conn.rollback()
                raise

    def _read(self, fn):
        with self._lock:
            return fn(self._conn)

    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row], booked: bool = False) -> Optional[dict]:
        if row is None:
            return None
        d = dict(row)
        d["booked"] = booked
        d["to"] = d["recipient"]          # 别名: 测试契约用 to
        d["resource"] = d["recipient"]    # 别名: 设计文档用 resource
        d["fingerprint"] = json.loads(d["fingerprint"])
        return d

    # ── 查询 ──────────────────────────────────────────────────────────
    def get(self, intent_id: int) -> Optional[dict]:
        def _q(conn):
            row = conn.execute("SELECT * FROM intents WHERE id=?", (intent_id,)).fetchone()
            booked = False
            if row is not None:
                b = conn.execute("SELECT 1 FROM bookings WHERE intent_id=?",
                                 (intent_id,)).fetchone()
                booked = b is not None
            return self._row_to_dict(row, booked)
        return self._read(_q)

    def list_by_status(self, *statuses: str) -> list[dict]:
        """只读: 按状态列出 intent (Phase C 集成/recovery 用; 不改变任何语义)。"""
        def _q(conn):
            if not statuses:
                return []
            marks = ",".join("?" * len(statuses))
            rows = conn.execute(
                f"SELECT * FROM intents WHERE status IN ({marks}) ORDER BY id",
                statuses).fetchall()
            return [self._row_to_dict(r, self._is_booked(conn, r["id"])) for r in rows]
        return self._read(_q)

    # ── 创建 (幂等 create-or-get + fingerprint 冲突) ──────────────────
    def create_or_get(self, agent: str, to: str, idem_key: str, amount: float,
                      fingerprint: dict, currency: str = "USD") -> dict:
        """建立 durable reservation (幂等 create-or-get)。

        - 同 scope 同 fingerprint → 返回已有 intent (含持久化的 provider_key)
        - 同 scope 异 fingerprint → IntentConflict
        - RECONCILED_FAILED (active=0) 释放 scope 后 → 开全新 intent (新 provider_key)
        structural validation (review fix #3): bool/NaN/±Inf → ValueError —
        非有限数值不能进 durable ledger (业务 policy 校验在上游 guard)。
        """
        agent_s = agent if isinstance(agent, str) else str(agent)
        to_s = to if isinstance(to, str) else str(to)
        idem_s = idem_key if isinstance(idem_key, str) else str(idem_key)
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise ValueError(f"amount must be a finite int/float, got {amount!r}")
        amount_f = float(amount)
        if not math.isfinite(amount_f):
            raise ValueError(f"amount must be finite, got {amount!r}")
        fp_s = _canon_fp(fingerprint)

        def _create(conn):
            # 1) 幂等: 命中活跃 scope 记录
            row = conn.execute(
                "SELECT * FROM intents WHERE agent=? AND recipient=? AND idem_key=? AND active=1",
                (agent_s, to_s, idem_s)).fetchone()
            if row is not None:
                if row["fingerprint"] != fp_s:
                    raise IntentConflict(
                        f"idempotency key conflict: same scope (agent={agent_s}, to={to_s}, "
                        f"key={idem_s}) reused with a different request fingerprint")
                return self._row_to_dict(row, self._is_booked(conn, row["id"]))
            # 2) 新建 RESERVED (provider_key 生成一次并持久化, 重试复用; 调用方不可自供)
            provider_key = secrets.token_hex(16)
            ts = _now()
            cur = conn.execute(
                "INSERT INTO intents (agent, recipient, idem_key, amount, currency, fingerprint, "
                "provider_key, status, active, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                (agent_s, to_s, idem_s, amount_f, currency, fp_s, provider_key,
                 RESERVED, ts, ts))
            return self._row_to_dict(self._fetch(conn, cur.lastrowid), False)

        try:
            return self._write(_create)
        except sqlite3.IntegrityError as e:
            # 并发双写撞 UNIQUE(active scope): 重读 winner 裁决;
            # 重读不到 winner → 不是并发 scope race → 原样抛回原异常 (review fix #5)
            def _retry(conn):
                row = conn.execute(
                    "SELECT * FROM intents WHERE agent=? AND recipient=? AND idem_key=? AND active=1",
                    (agent_s, to_s, idem_s)).fetchone()
                if row is None:
                    raise e  # 原异常 (嵌套闭包捕获, 显式 re-raise)
                if row["fingerprint"] != fp_s:
                    raise IntentConflict(
                        f"idempotency key conflict: same scope reused with a different request "
                        f"fingerprint (concurrent create)")
                return self._row_to_dict(row, self._is_booked(conn, row["id"]))
            return self._write(_retry)

    # ── 状态迁移 (全部 CAS + BEGIN IMMEDIATE) ─────────────────────────
    def reserve(self, intent_id: int) -> bool:
        """Pre-dispatch eligibility check (不是 atomic reservation op —
        reservation 由 create_or_get() 建立)。RESERVED → True, 其它 → False (良性)."""
        def _f(conn):
            row = conn.execute("SELECT status FROM intents WHERE id=?",
                               (intent_id,)).fetchone()
            return row is not None and row["status"] == RESERVED
        return self._read(_f)

    def mark_in_flight(self, intent_id: int) -> bool:
        """RESERVED → IN_FLIGHT (CAS)。已 IN_FLIGHT / UNKNOWN / 终态 → False。"""
        def _f(conn):
            cur = conn.execute(
                "UPDATE intents SET status=?, updated_at=? WHERE id=? AND status=? AND active=1",
                (IN_FLIGHT, _now(), intent_id, RESERVED))
            return cur.rowcount == 1
        return self._write(_f)

    def mark_succeeded(self, intent_id: int) -> bool:
        """IN_FLIGHT → SUCCEEDED (直接 ack 路径)。UNKNOWN 上调用 = 绕过 reconcile → raise。"""
        def _f(conn):
            row = conn.execute("SELECT status FROM intents WHERE id=?",
                               (intent_id,)).fetchone()
            if row is None:
                return False
            if row["status"] == UNKNOWN:
                raise InvalidTransition(
                    f"intent {intent_id} is UNKNOWN: mark_succeeded is not allowed, "
                    f"must reconcile first (UNKNOWN ≠ retry allowed)")
            if row["status"] == SUCCEEDED:
                return False  # 重复 ack = no-op
            if row["status"] != IN_FLIGHT:
                raise InvalidTransition(
                    f"intent {intent_id} is {row['status']}: only IN_FLIGHT can "
                    f"mark_succeeded")
            cur = conn.execute(
                "UPDATE intents SET status=?, updated_at=? WHERE id=? AND status=?",
                (SUCCEEDED, _now(), intent_id, IN_FLIGHT))
            return cur.rowcount == 1
        return self._write(_f)

    def mark_unknown(self, intent_id: int) -> bool:
        """IN_FLIGHT → UNKNOWN (超时/崩溃)。已是 UNKNOWN → no-op True。"""
        def _f(conn):
            row = conn.execute("SELECT status FROM intents WHERE id=?",
                               (intent_id,)).fetchone()
            if row is None:
                return False
            if row["status"] == UNKNOWN:
                return True
            if row["status"] != IN_FLIGHT:
                raise InvalidTransition(
                    f"intent {intent_id} is {row['status']}: only IN_FLIGHT can "
                    f"mark_unknown")
            cur = conn.execute(
                "UPDATE intents SET status=?, updated_at=? WHERE id=? AND status=?",
                (UNKNOWN, _now(), intent_id, IN_FLIGHT))
            return cur.rowcount == 1
        return self._write(_f)

    def cancel_reserved(self, intent_id: int) -> bool:
        """RESERVED → RECONCILED_FAILED (active=0) — dispatch 前取消, **不带 proof**。

        RESERVED 的定义 = 从未成功 claim dispatch, durable state 自己就能证明
        「从未通过 SpendShield flow 发出」→ 与 UNKNOWN 的 proof-backed reconcile
        是两个 trust boundary, 不需要伪造 rail proof (review fix #6)。

        - RESERVED → True
        - 已是 RECONCILED_FAILED → False (幂等 no-op)
        - IN_FLIGHT / UNKNOWN / SUCCEEDED → InvalidTransition (dispatch boundary 不可模糊 —
          静默 False 会让上层误以为「已安全取消」)
        """
        def _f(conn):
            row = conn.execute("SELECT status FROM intents WHERE id=?",
                               (intent_id,)).fetchone()
            if row is None:
                return False
            if row["status"] == TERMINAL_FAILED:
                return False
            if row["status"] != RESERVED:
                raise InvalidTransition(
                    f"intent {intent_id} is {row['status']}: cancel_reserved is only valid "
                    f"from RESERVED (dispatch boundary)")
            cur = conn.execute(
                "UPDATE intents SET status=?, active=0, updated_at=? WHERE id=? AND status=?",
                (TERMINAL_FAILED, _now(), intent_id, RESERVED))
            return cur.rowcount == 1
        return self._write(_f)

    def reconcile(self, intent_id: int, outcome: str, proof: str) -> bool:
        """IN_FLIGHT/UNKNOWN → SUCCEEDED | RECONCILED_FAILED, 必须带 proof。

        RECONCILED_FAILED 是终态: active=0 释放 scope, 同 key 可开新 intent。
        proof = rail txn id / 可信执行证据 (唯一能推进 UNKNOWN 的入口)。
        """
        if outcome not in (SUCCEEDED, TERMINAL_FAILED):
            raise InvalidTransition(f"reconcile outcome must be SUCCEEDED or "
                                    f"{TERMINAL_FAILED}, got {outcome!r}")
        if not proof or not isinstance(proof, str):
            raise InvalidTransition("reconcile requires a proof (rail txn id / evidence)")

        def _f(conn):
            row = conn.execute("SELECT status FROM intents WHERE id=?",
                               (intent_id,)).fetchone()
            if row is None:
                return False
            if row["status"] in (SUCCEEDED, TERMINAL_FAILED):
                return False  # 已定论 = no-op
            if row["status"] not in (IN_FLIGHT, UNKNOWN):
                raise InvalidTransition(
                    f"intent {intent_id} is {row['status']}: only IN_FLIGHT/UNKNOWN "
                    f"can be reconciled")
            active = 0 if outcome == TERMINAL_FAILED else 1
            cur = conn.execute(
                "UPDATE intents SET status=?, proof=?, active=?, updated_at=? "
                "WHERE id=? AND status=?",
                (outcome, proof, active, _now(), intent_id, row["status"]))
            return cur.rowcount == 1
        return self._write(_f)

    # ── 消费: durable booking (方案 A — 见设计文档 §5) ─────────────────
    @staticmethod
    def _insert_booking(conn, intent_row) -> bool:
        """唯一 booking 插入原语 (review fix #9)。调用方必须已持有写事务。

        成功 → True (赢家)。IntegrityError 上抛, 由调用方分类 —
        本函数不吞任何 integrity 故障。
        """
        conn.execute(
            "INSERT INTO bookings (booking_id, intent_id, agent, recipient, amount, "
            "currency, created_at) VALUES (?,?,?,?,?,?,?)",
            (secrets.token_hex(16), intent_row["id"], intent_row["agent"],
             intent_row["recipient"], intent_row["amount"], intent_row["currency"], _now()))
        return True

    def consume_once(self, intent_id: int) -> bool:
        """SUCCEEDED 的 intent 入 booking ledger, 恰好一次 (review fix #8/#10)。

        - INSERT 成功 → True (赢家; 这是唯一的 durable booking commit)
        - IntegrityError 后查 bookings.intent_id: 存在 → False (already consumed);
          不存在 → raise 原异常 (booking_id 碰撞 / FK / schema 故障不伪装成已消费)
        - 状态非 SUCCEEDED → False (调用方用 get().status 区分「还没成」与「已消费」)
        """
        def _f(conn):
            row = conn.execute("SELECT * FROM intents WHERE id=?",
                               (intent_id,)).fetchone()
            if row is None or row["status"] != SUCCEEDED:
                return False
            try:
                self._insert_booking(conn, row)
                return True
            except sqlite3.IntegrityError:
                # 分类: 只有 intent_id 已有 booking 才是 already-consumed
                if conn.execute("SELECT 1 FROM bookings WHERE intent_id=?",
                                (intent_id,)).fetchone() is not None:
                    return False
                raise  # 其它 integrity 故障 → 原样上抛, 绝不伪装
        return self._write(_f)

    def replay_succeeded(self):
        """Startup rebuild (guardrail #4, review fix #9): 给每个 SUCCEEDED intent
        补齐 booking 行 (与 consume_once 共用 _insert_booking, 无嵌套事务)。

        幂等: rebuild(rebuild(state)) == rebuild(state)。
        返回 (本次新增 booking 数, 当前 SUCCEEDED intent 总数)。
        整个遍历 = 一个 IMMEDIATE 事务: crash 在 COMMIT 前 → 全回滚重来;
        COMMIT 后 → 全落库, 下次全 no-op。不 book UNKNOWN/IN_FLIGHT
        (它们必须等 reconciler 证明, replay 不替它拍板)。
        """
        def _f(conn):
            rows = conn.execute(
                "SELECT * FROM intents WHERE status=? AND active=1",
                (SUCCEEDED,)).fetchall()
            added = 0
            for r in rows:
                if conn.execute("SELECT 1 FROM bookings WHERE intent_id=?",
                                (r["id"],)).fetchone() is None:
                    self._insert_booking(conn, r)  # PK 已查不撞; booking_id 撞 → raise → 整段回滚
                    added += 1
            return added, len(rows)
        return self._write(_f)

    # ── 内部小工具 ────────────────────────────────────────────────────
    @staticmethod
    def _fetch(conn, intent_id):
        return conn.execute("SELECT * FROM intents WHERE id=?",
                            (intent_id,)).fetchone()

    @staticmethod
    def _is_booked(conn, intent_id) -> bool:
        return conn.execute("SELECT 1 FROM bookings WHERE intent_id=?",
                            (intent_id,)).fetchone() is not None
