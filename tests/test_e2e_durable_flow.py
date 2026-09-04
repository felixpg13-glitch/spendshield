# -*- coding: utf-8 -*-
"""Phase C Step 3 — End-to-end durable flow with a realistic FakeRail (issue #2).

目的 (Felix gate): 接近真实用户的完整链路 —
    application → SpendShield durable adapter → FakeRail
    → crash simulation → new process → recover → retry
关键 assertion (每条场景):
    external executions == 1
    durable bookings == 1

FakeRail 语义: execute() 落账 (provider 幂等), 可选 ack 丢失; lookup 三态。
"进程重启" = 同一 sqlite 文件 + 新 guard/store/client (rail 是外部世界, 跨进程存活)。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spendshield import SpendShield
from spendshield.adapters.x402_intent import X402IntentClient
from spendshield.intent import SqliteIntentStore


class _AckLost(Exception):
    """rail 已 commit 但 ack 丢失 (应用看不到结果)。"""


class _FakeRail:
    """外部 rail: 按 provider_key 幂等; 支持 normal / drop_ack / indeterminate。"""

    def __init__(self):
        self.executions = []          # 已落账 (外部世界事实)
        self.drop_ack = False
        self.lookup_mode = "normal"   # normal | indeterminate

    def execute(self, provider_key, amount, to):
        ex = next((e for e in self.executions if e["provider_key"] == provider_key), None)
        if ex is None:
            ex = {"provider_key": provider_key, "amount": amount, "to": to,
                  "outcome": "committed", "proof": f"rail:txn:{provider_key}"}
            self.executions.append(ex)
            if self.drop_ack:
                raise _AckLost("committed but acknowledgement lost")  # 钱已动, 应用看不到
        return dict(ex)

    def lookup(self, provider_key):
        if self.lookup_mode == "indeterminate":
            return "INDETERMINATE"
        return "COMMITTED" if any(e["provider_key"] == provider_key for e in self.executions) \
            else "NOT_EXECUTED_PROVEN"


def _fp(amount):
    return {"recipient": "api.example.com", "amount": amount, "currency": "USD",
            "resource": "api.example.com", "payment_method": "x402"}


def _boot(db):
    """启动一个"进程": 新 guard + store + client (同一 sqlite 文件)。"""
    guard = SpendShield(dry_run=False, budget=1000.0, approve_new_recipient=False,
                        allow_unknown=True)
    store = SqliteIntentStore(db)
    return store, guard, X402IntentClient(guard, store)


def _booked_intents(store):
    """durable bookings: SUCCEEDED 且已入 ledger 的 intent。"""
    return [i for i in store.list_by_status("SUCCEEDED") if i["booked"]]


# ─────────────────────────────────────────────────────────────────────
# S1: happy path — 一次成功支付 = 1 外部执行 + 1 durable booking
# ─────────────────────────────────────────────────────────────────────
def test_e2e_happy_path(tmp_path):
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()
    store, guard, client = _boot(db)

    out = client.protect(agent="bot", to="api.example.com", amount=5.0,
                         idem_key="E2E-1", fingerprint=_fp(5.0))
    assert out["status"] == "IN_FLIGHT"
    res = rail.execute(out["provider_key"], 5.0, "api.example.com")
    assert client.complete(out["intent_id"], res) is True

    assert len(rail.executions) == 1
    assert len(_booked_intents(store)) == 1
    assert guard.spent == 5.0
    # 重启后 recover → no-op, 数字不变
    store2, guard2, client2 = _boot(db)
    assert client2.recover(rail)["booked"] == 0
    assert len(rail.executions) == 1 and len(_booked_intents(store2)) == 1


# ─────────────────────────────────────────────────────────────────────
# S2 (headline, flowpatch 原洞 E2E): commit + ack 丢失 + 进程崩溃 →
# 重启 recover → 1 外部执行 + 1 durable booking; 重试同 key 绝不二次 dispatch
# ─────────────────────────────────────────────────────────────────────
def test_e2e_crash_after_commit_ack_lost(tmp_path):
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()
    rail.drop_ack = True

    # ── 进程 A: 应用发起支付, rail 已扣款但 ack 丢失, 应用崩溃 ──
    store_a, _, client_a = _boot(db)
    out = client_a.protect(agent="bot", to="api.example.com", amount=5.0,
                           idem_key="E2E-2", fingerprint=_fp(5.0))
    try:
        rail.execute(out["provider_key"], 5.0, "api.example.com")
    except _AckLost:
        pass  # 应用在收到结果前崩了 — complete 从未发生
    assert len(rail.executions) == 1, "rail 侧钱已动"
    # (进程 A 死亡, 无 recover)

    # ── 进程 B: 重启, recover ──
    rail.drop_ack = False
    store_b, guard_b, client_b = _boot(db)
    report = client_b.recover(rail)
    assert report["booked"] == 1 and report["failed"] == 0
    assert guard_b.spent == 5.0
    assert len(rail.executions) == 1, "绝不能二次外部执行"
    assert len(_booked_intents(store_b)) == 1

    # ── 应用重试同一逻辑支付: 命中 SUCCEEDED intent, 绝不 dispatch ──
    retry = client_b.protect(agent="bot", to="api.example.com", amount=5.0,
                             idem_key="E2E-2", fingerprint=_fp(5.0))
    assert retry["status"] == "SUCCEEDED"
    assert retry["provider_key"] == out["provider_key"]
    assert len(rail.executions) == 1, "重试不得产生第二次外部执行"
    assert len(_booked_intents(store_b)) == 1, "durable bookings 恒为 1"


# ─────────────────────────────────────────────────────────────────────
# S3: dispatch 前崩溃 + NOT_EXECUTED_PROVEN → 释放 → 同 key 重试成功
#     全程 1 外部执行 + 1 durable booking (失败的 intent 不入账)
# ─────────────────────────────────────────────────────────────────────
def test_e2e_crash_before_dispatch_proven_absent(tmp_path):
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()

    store, _, client = _boot(db)
    out = client.protect(agent="bot", to="api.example.com", amount=5.0,
                         idem_key="E2E-3", fingerprint=_fp(5.0))
    # 崩溃: rail 从未收到 → lookup NOT_EXECUTED_PROVEN
    assert rail.lookup(out["provider_key"]) == "NOT_EXECUTED_PROVEN"

    report = client.recover(rail)
    assert report["failed"] == 1 and report["booked"] == 0

    # 同逻辑 key 重试 = 全新 intent → 这次真发
    retry = client.protect(agent="bot", to="api.example.com", amount=5.0,
                           idem_key="E2E-3", fingerprint=_fp(5.0))
    assert retry["status"] == "IN_FLIGHT"
    res = rail.execute(retry["provider_key"], 5.0, "api.example.com")
    assert client.complete(retry["intent_id"], res) is True

    assert len(rail.executions) == 1, "全程只执行一次"
    assert len(_booked_intents(store)) == 1, "只 book 成功的那一次"
    assert client.recover(rail)["booked"] == 0


# ─────────────────────────────────────────────────────────────────────
# S4: rail 不可达 (INDETERMINATE) → fail-closed; rail 恢复 COMMITTED →
# recover 收敛: 1 执行 + 1 booking
# ─────────────────────────────────────────────────────────────────────
def test_e2e_lookup_unavailable_then_committed(tmp_path):
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()
    rail.drop_ack = True

    store, _, client = _boot(db)
    out = client.protect(agent="bot", to="api.example.com", amount=5.0,
                         idem_key="E2E-4", fingerprint=_fp(5.0))
    try:
        rail.execute(out["provider_key"], 5.0, "api.example.com")
    except _AckLost:
        pass
    assert client.complete(out["intent_id"], {"outcome": "ambiguous"}) is False
    assert store.get(out["intent_id"])["status"] == "UNKNOWN"

    # rail 查询不可用 → recover 不动任何状态
    rail.lookup_mode = "indeterminate"
    report = client.recover(rail)
    assert report["booked"] == 0 and report["failed"] == 0
    assert store.get(out["intent_id"])["status"] == "UNKNOWN"

    # rail 恢复且证明 COMMITTED → 收敛
    rail.lookup_mode = "normal"
    store2, guard2, client2 = _boot(db)
    report2 = client2.recover(rail)
    assert report2["booked"] == 1
    assert guard2.spent == 5.0
    assert len(rail.executions) == 1
    assert len(_booked_intents(store2)) == 1


# ─────────────────────────────────────────────────────────────────────
# S5: 成功后重复完成 / 重复 recover — 数字永不漂移
# ─────────────────────────────────────────────────────────────────────
def test_e2e_no_drift_after_success(tmp_path):
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()
    store, guard, client = _boot(db)

    out = client.protect(agent="bot", to="api.example.com", amount=5.0,
                         idem_key="E2E-5", fingerprint=_fp(5.0))
    res = rail.execute(out["provider_key"], 5.0, "api.example.com")
    assert client.complete(out["intent_id"], res) is True
    assert client.complete(out["intent_id"], res) is False   # 二次 complete
    assert client.recover(rail)["booked"] == 0              # recover no-op

    for _ in range(3):  # 反复 recover 不漂移
        store2, guard2, client2 = _boot(db)
        r = client2.recover(rail)
        assert r["booked"] == 0
        assert guard2.spent == 5.0
    assert len(rail.executions) == 1
    assert len(_booked_intents(store)) == 1
    assert guard.spent == 5.0
