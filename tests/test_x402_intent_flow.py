# -*- coding: utf-8 -*-
"""Phase C RED tests — x402 intent client flow (issue #2)

设计: docs/intent-x402-integration.md (三态 lookup / binding / cache projection)。
本文件 = 集成契约测试, 全部 RED 直到 spendshield/adapters/x402_intent.py 实现。

审查焦点 (Felix gate, 三条钉死):
  ① guard.book() 在 intent path = cache projection, ledger row 才是 correctness
     boundary, startup 从 bookings rebuild cache
  ② complete()/reconciliation 必须验证 rail result 与 intent.provider_key 的
     binding — intent_id 不是「这个 result 属于谁」的 trust boundary
  ③ rail lookup 三态: COMMITTED / NOT_EXECUTED_PROVEN / INDETERMINATE —
     普通 not-found 绝不自动当 proven absent

契约 (草案):
    client = X402IntentClient(guard, store)
    out = client.protect(agent, to, amount, idem_key, fingerprint)
          # 返回 {intent_id, provider_key, status}; status != "IN_FLIGHT"
          # ⇒ recovery, 绝不 dispatch
    client.complete(intent_id, rail_result)
          # rail_result = {provider_key, outcome, proof}
          # outcome ∈ committed | failed | ambiguous
          # committed/failed 必须 binding 校验 (provider_key == intent 的);
          # 不匹配/缺失 → raise, intent 不变
          # 返回 True ⇔ 本次调用执行了 durable booking
    client.recover(rail_lookup)
          # ①歧义 pass (三态裁决 IN_FLIGHT/UNKNOWN)
          # ②booking pass (consume_once → guard.book = cache projection)
          # 返回 {"booked", "failed", "indeterminate"}
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from spendshield import SpendShield


def _layer():
    """SqliteIntentStore (Phase B, 已实现)。"""
    from spendshield.intent import SqliteIntentStore
    return SqliteIntentStore


def _client():
    """X402IntentClient 类。尚未实现 → 显式 RED。"""
    try:
        from spendshield.adapters.x402_intent import X402IntentClient  # type: ignore
        return X402IntentClient
    except Exception as e:
        pytest.fail(f"[RED] x402 intent adapter 未实现 (Phase C): {e!r}")


def _fp(amount):
    return {"recipient": "api.example.com", "amount": amount, "currency": "USD",
            "resource": "api.example.com", "payment_method": "x402"}


def _guard():
    return SpendShield(dry_run=False, budget=1000.0, approve_new_recipient=False,
                       allow_unknown=True)


class _FakeRail:
    """外部 rail: 按 provider_key 幂等; lookup 三态 (COMMITTED /
    NOT_EXECUTED_PROVEN / INDETERMINATE)。INDETERMINATE = rail 无法给出权威结论。"""

    def __init__(self, mode="normal"):
        self.executions = []
        self.mode = mode  # normal | indeterminate

    def pay(self, provider_key, amount, to):
        for ex in self.executions:
            if ex["provider_key"] == provider_key:
                return ex  # provider 侧幂等
        ex = {"provider_key": provider_key, "amount": amount, "to": to,
              "outcome": "committed", "proof": f"rail:txn:{provider_key}"}
        self.executions.append(ex)
        return ex

    def lookup(self, provider_key):
        if self.mode == "indeterminate":
            return "INDETERMINATE"  # 无法权威证明任何一边
        for ex in self.executions:
            if ex["provider_key"] == provider_key:
                return "COMMITTED"
        return "NOT_EXECUTED_PROVEN"


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

    res = rail.pay(out["provider_key"], 5.0, "api.example.com")   # caller 执行
    assert client.complete(out["intent_id"], res) is True
    assert guard.spent == 5.0, "恰好 book 一次 (cache projection 同步)"

    assert client.complete(out["intent_id"], res) is False        # 二次 complete no-op
    assert store.replay_succeeded() == (0, 1), "replay 必须 no-op"
    assert guard.spent == 5.0


# ─────────────────────────────────────────────────────────────────────
# test 2 (boundary 2, flowpatch 原洞): rail committed 后 crash,
# 重启 recover → 三态 lookup COMMITTED → book 一次, 不二次执行
# ─────────────────────────────────────────────────────────────────────
def test_crash_after_rail_commit_recover_books_once(tmp_path):
    SqliteIntentStore, X402IntentClient = _layer(), _client()
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()

    # ── 进程 A: protect + 外部已扣款, 未 complete 就崩 ──
    store_a = SqliteIntentStore(db)
    out = X402IntentClient(_guard(), store_a).protect(
        agent="bot", to="api.example.com", amount=5.0,
        idem_key="P-002", fingerprint=_fp(5.0))
    rail.pay(out["provider_key"], 5.0, "api.example.com")         # 钱已动
    # (crash: intent 停 IN_FLIGHT)

    # ── 进程 B: 重启, recover ──
    store_b = SqliteIntentStore(db)
    guard_b = _guard()
    client_b = X402IntentClient(guard_b, store_b)
    report = client_b.recover(rail)
    assert report["booked"] == 1, "recover 必须补齐 1 笔 durable booking + cache"
    assert report["failed"] == 0
    assert guard_b.spent == 5.0, "cache 从 ledger rebuild"
    assert len(rail.executions) == 1, "绝不能二次外部执行"

    report2 = client_b.recover(rail)
    assert report2["booked"] == 0
    assert guard_b.spent == 5.0


# ─────────────────────────────────────────────────────────────────────
# test 3 (boundary 1): dispatch 前 crash + rail NOT_EXECUTED_PROVEN →
# 释放 scope → 同逻辑 key 重试开新 intent → 只执行一次、book 一次
# ─────────────────────────────────────────────────────────────────────
def test_crash_before_dispatch_proven_absent_retry(tmp_path):
    SqliteIntentStore, X402IntentClient = _layer(), _client()
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()

    store = SqliteIntentStore(db)
    out = X402IntentClient(_guard(), store).protect(
        agent="bot", to="api.example.com", amount=5.0,
        idem_key="P-003", fingerprint=_fp(5.0))
    # (crash: rail 从未收到 — lookup 权威 NOT_EXECUTED_PROVEN)
    assert rail.lookup(out["provider_key"]) == "NOT_EXECUTED_PROVEN"

    client = X402IntentClient(_guard(), store)
    report = client.recover(rail)
    assert report["failed"] == 1, "NOT_EXECUTED_PROVEN → RECONCILED_FAILED 释放 scope"
    assert report["booked"] == 0, "没发生的事不能 book"

    # 同逻辑 key 重试 = 全新 intent (新 provider_key), 这次真发
    retry = client.protect(agent="bot", to="api.example.com", amount=5.0,
                           idem_key="P-003", fingerprint=_fp(5.0))
    assert retry["status"] == "IN_FLIGHT"
    assert retry["provider_key"] != out["provider_key"]
    res = rail.pay(retry["provider_key"], 5.0, "api.example.com")
    assert client.complete(retry["intent_id"], res) is True

    assert len(rail.executions) == 1, "全程只执行一次"
    assert client.recover(rail)["booked"] == 0


# ─────────────────────────────────────────────────────────────────────
# test 4: ambiguous → UNKNOWN 不可被调用方重试绕过; lookup INDETERMINATE →
# 保持原状 (fail-closed), 不 book 不释放; 后来 COMMITTED → recover 收敛
# ─────────────────────────────────────────────────────────────────────
def test_ambiguous_unknown_fail_closed(tmp_path):
    SqliteIntentStore, X402IntentClient = _layer(), _client()
    db = str(tmp_path / "intents.db")
    store = SqliteIntentStore(db)
    client = X402IntentClient(_guard(), store)

    out = client.protect(agent="bot", to="api.example.com", amount=5.0,
                         idem_key="P-004", fingerprint=_fp(5.0))
    # 超时 → ambiguous (无 rail result, 无 binding 可言)
    assert client.complete(out["intent_id"], {"outcome": "ambiguous"}) is False
    assert store.get(out["intent_id"])["status"] == "UNKNOWN"

    # 调用方重试同 key → 必须拿到已有 intent, 绝不 dispatch
    retry = client.protect(agent="bot", to="api.example.com", amount=5.0,
                           idem_key="P-004", fingerprint=_fp(5.0))
    assert retry["intent_id"] == out["intent_id"]
    assert retry["status"] == "UNKNOWN", "UNKNOWN ≠ retry allowed"

    # rail INDETERMINATE → recover fail-closed: 不动任何状态
    report = client.recover(_FakeRail(mode="indeterminate"))
    assert store.get(out["intent_id"])["status"] == "UNKNOWN"
    assert report["booked"] == 0 and report["failed"] == 0
    assert report["indeterminate"] >= 1

    # rail 恢复且 COMMITTED → recover 收敛: durable booking + cache
    rail = _FakeRail()
    rail.pay(out["provider_key"], 5.0, "api.example.com")
    guard = _guard()
    client2 = X402IntentClient(guard, SqliteIntentStore(db))
    report2 = client2.recover(rail)
    assert report2["booked"] == 1
    assert guard.spent == 5.0
    assert store.get(out["intent_id"])["status"] == "SUCCEEDED"


# ─────────────────────────────────────────────────────────────────────
# test 5 (gate ②): rail result 必须绑定 intent.provider_key —
# 拿另一个 intent 的 result 标记本 intent → 拒绝, intent 不变
# ─────────────────────────────────────────────────────────────────────
def test_complete_rejects_unbound_rail_result(tmp_path):
    SqliteIntentStore, X402IntentClient = _layer(), _client()
    store = SqliteIntentStore(str(tmp_path / "intents.db"))
    client = X402IntentClient(_guard(), store)
    rail = _FakeRail()

    a = client.protect(agent="bot", to="api.example.com", amount=5.0,
                       idem_key="P-005a", fingerprint=_fp(5.0))
    b = client.protect(agent="bot", to="api.example.com", amount=5.0,
                       idem_key="P-005b", fingerprint=_fp(5.0))
    res_b = rail.pay(b["provider_key"], 5.0, "api.example.com")

    # 用 B 的 committed result 标记 A → 必须拒绝, A 不被标 SUCCEEDED
    with pytest.raises(Exception):
        client.complete(a["intent_id"], res_b)
    assert store.get(a["intent_id"])["status"] == "IN_FLIGHT"
    assert store.get(a["intent_id"])["booked"] is False

    # 缺 provider_key 的 result 同样拒绝
    with pytest.raises(Exception):
        client.complete(a["intent_id"], {"outcome": "committed", "proof": "rail:txn:??"})

    # 自己的 result 正常
    res_a = rail.pay(a["provider_key"], 5.0, "api.example.com")
    assert client.complete(a["intent_id"], res_a) is True
