# -*- coding: utf-8 -*-
"""Phase C RED tests — x402 intent client flow (issue #2)

设计: docs/intent-x402-integration.md (集成序列 + 两个 crash boundary 分析)。
本文件 = 集成契约测试, 全部 RED 直到 spendshield/adapters/x402_intent.py 实现。

审查焦点 (Felix gate):
  - boundary 1: mark_in_flight 之后、真正 rail dispatch 之前的 crash
  - boundary 2: rail committed 之后、mark_succeeded 之前的 crash (flowpatch 原洞)

契约 (草案, 以 review 为准):
    client = X402IntentClient(guard, store)
    out = client.protect(agent, to, amount, idem_key, fingerprint)
          # authorize → create_or_get → reserve → mark_in_flight
          # 返回 {intent_id, provider_key, status}; status 可能不是 IN_FLIGHT
          # (已有 UNKNOWN/SUCCEEDED intent → 走 recovery, 绝不 dispatch)
    client.complete(intent_id, "committed"|"failed"|"ambiguous", proof="")
          # committed → settle (consume_once→guard.book) 恰好一次; 返回是否 booked
          # failed → reconcile(RECONCILED_FAILED); ambiguous → mark_unknown
    client.recover(rail_lookup)  # ①歧义 pass(rail 证据裁决 IN_FLIGHT/UNKNOWN)
                                 # ②booking pass(replay_succeeded)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from spendshield import SpendShield


def _layer():
    """返回 SqliteIntentStore (Phase B, 已实现)。"""
    from spendshield.intent import SqliteIntentStore
    return SqliteIntentStore


def _client():
    """返回 X402IntentClient 类。尚未实现 → 显式 RED。"""
    try:
        from spendshield.adapters.x402_intent import X402IntentClient  # type: ignore
        return X402IntentClient
    except Exception as e:
        pytest.fail(f"[RED] x402 intent adapter 未实现 (Phase C): {e!r}")


def _fp(amount):
    return {"recipient": "api.example.com", "amount": amount, "currency": "USD",
            "resource": "api.example.com", "payment_method": "x402"}


def _guard():
    return SpendShield(dry_run=False, budget=1000.0, approve_new_recipient=False)


class _RailUnavailable(Exception):
    pass


class _FakeRail:
    """外部 rail: 按 provider_key 幂等; lookup 可切换 committed/absent/unreachable。"""

    def __init__(self, mode="reachable"):
        self.executions = []
        self.mode = mode

    def pay(self, provider_key, amount, to):
        for ex in self.executions:
            if ex["key"] == provider_key:
                return ex
        ex = {"key": provider_key, "amount": amount, "to": to, "committed": True}
        self.executions.append(ex)
        return ex

    def lookup(self, provider_key):
        if self.mode == "unreachable":
            raise _RailUnavailable(f"rail unreachable (lookup {provider_key})")
        for ex in self.executions:
            if ex["key"] == provider_key:
                return ex
        return None  # authoritative absent


# ─────────────────────────────────────────────────────────────────────
# test 1: ack-success 路径恰好 book 一次; replay / 二次 complete 全 no-op
# ─────────────────────────────────────────────────────────────────────
def test_ack_success_books_once(tmp_path):
    SqliteIntentStore, X402IntentClient = _layer(), _client()
    store = SqliteIntentStore(str(tmp_path / "intents.db"))
    guard = _guard()
    client = X402IntentClient(guard, store)
    rail = _FakeRail()

    out = client.protect(agent="bot", to="api.example.com", amount=5.0,
                         idem_key="P-001", fingerprint=_fp(5.0))
    assert out["status"] == "IN_FLIGHT"
    assert out["provider_key"]

    rail.pay(out["provider_key"], 5.0, "api.example.com")      # caller 执行
    assert client.complete(out["intent_id"], "committed", proof="rail:txn:P-001") is True
    assert guard.spent == 5.0, "恰好 book 一次"

    assert client.complete(out["intent_id"], "committed", proof="rail:txn:P-001") is False
    assert store.replay_succeeded() == (0, 1), "replay 必须 no-op"
    assert guard.spent == 5.0


# ─────────────────────────────────────────────────────────────────────
# test 2 (boundary 2, flowpatch 原洞): rail committed 后 crash,
# 重启 recover → 靠 durable provider_key 查 rail → book 一次, 不二次执行
# ─────────────────────────────────────────────────────────────────────
def test_crash_after_rail_commit_recover_books_once(tmp_path):
    SqliteIntentStore, X402IntentClient = _layer(), _client()
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()

    # ── 进程 A: protect + 外部已扣款, 未 complete 就崩 ──
    store_a = SqliteIntentStore(db)
    client_a = X402IntentClient(_guard(), store_a)
    out = client_a.protect(agent="bot", to="api.example.com", amount=5.0,
                           idem_key="P-002", fingerprint=_fp(5.0))
    rail.pay(out["provider_key"], 5.0, "api.example.com")      # 钱已动
    # (crash: mark_succeeded/consume 从未发生, intent 停 IN_FLIGHT)

    # ── 进程 B: 重启, recover ──
    store_b = SqliteIntentStore(db)
    guard_b = _guard()
    client_b = X402IntentClient(guard_b, store_b)
    report = client_b.recover(rail)
    assert report["booked"] == 1, "recover 必须补齐 1 笔 booking"
    assert guard_b.spent == 5.0
    assert len(rail.executions) == 1, "绝不能二次外部执行"

    # 再 recover → no-op
    report2 = client_b.recover(rail)
    assert report2["booked"] == 0
    assert guard_b.spent == 5.0


# ─────────────────────────────────────────────────────────────────────
# test 3 (boundary 1): dispatch 前 crash + rail 权威 absent →
# recover 释放 scope → 同逻辑 key 重试开新 intent → 只执行一次、book 一次
# ─────────────────────────────────────────────────────────────────────
def test_crash_before_dispatch_absent_evidence_retry(tmp_path):
    SqliteIntentStore, X402IntentClient = _layer(), _client()
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()

    store = SqliteIntentStore(db)
    out = X402IntentClient(_guard(), store).protect(
        agent="bot", to="api.example.com", amount=5.0,
        idem_key="P-003", fingerprint=_fp(5.0))
    # (crash: rail 从未收到 — lookup 权威 absent)
    assert rail.lookup(out["provider_key"]) is None

    client = X402IntentClient(_guard(), store)
    report = client.recover(rail)
    assert report["failed"] == 1, "权威 absent → RECONCILED_FAILED 释放 scope"
    assert report["booked"] == 0, "没发生的事不能 book"

    # 同逻辑 key 重试 = 全新 intent (新 provider_key), 这次真发
    retry = client.protect(agent="bot", to="api.example.com", amount=5.0,
                           idem_key="P-003", fingerprint=_fp(5.0))
    assert retry["status"] == "IN_FLIGHT"
    assert retry["provider_key"] != out["provider_key"]
    rail.pay(retry["provider_key"], 5.0, "api.example.com")
    assert client.complete(retry["intent_id"], "committed", proof="rail:txn:P-003b") is True

    assert len(rail.executions) == 1, "全程只执行一次"
    assert client.recover(rail)["booked"] == 0


# ─────────────────────────────────────────────────────────────────────
# test 4: ambiguous → UNKNOWN 不可被调用方重试绕过; rail 不可达 →
# 保持 UNKNOWN (fail-closed), 不 book 不释放; rail 恢复后 recover 收敛
# ─────────────────────────────────────────────────────────────────────
def test_ambiguous_unknown_fail_closed(tmp_path):
    SqliteIntentStore, X402IntentClient = _layer(), _client()
    db = str(tmp_path / "intents.db")
    store = SqliteIntentStore(db)
    client = X402IntentClient(_guard(), store)

    out = client.protect(agent="bot", to="api.example.com", amount=5.0,
                         idem_key="P-004", fingerprint=_fp(5.0))
    # 超时 → ambiguous
    assert client.complete(out["intent_id"], "ambiguous") is False
    assert store.get(out["intent_id"])["status"] == "UNKNOWN"

    # 调用方重试同 key → 必须拿到已有 intent, 绝不 dispatch
    retry = client.protect(agent="bot", to="api.example.com", amount=5.0,
                           idem_key="P-004", fingerprint=_fp(5.0))
    assert retry["intent_id"] == out["intent_id"]
    assert retry["status"] == "UNKNOWN", "UNKNOWN ≠ retry allowed"

    # rail 不可达 → recover fail-closed: 不动任何状态
    rail_down = _FakeRail(mode="unreachable")
    report = client.recover(rail_down)
    assert store.get(out["intent_id"])["status"] == "UNKNOWN"
    assert report["booked"] == 0 and report["failed"] == 0

    # rail 恢复且证明 committed → recover 收敛: book 一次
    rail = _FakeRail()
    rail.pay(out["provider_key"], 5.0, "api.example.com")
    guard = _guard()
    client2 = X402IntentClient(guard, SqliteIntentStore(db))
    report2 = client2.recover(rail)
    assert report2["booked"] == 1
    assert guard.spent == 5.0
    assert store.get(out["intent_id"])["status"] == "SUCCEEDED"
