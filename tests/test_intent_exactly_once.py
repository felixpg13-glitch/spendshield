# -*- coding: utf-8 -*-
"""P0 RED tests — durable exactly-once payment intent (issue #2)

issue #2 (flowpatch-reliability) 指出 + 我方公开确认的 invariant 缺口:
    当前 authorize → 外部执行 → confirm/book 是两段式, 中间无持久化边界
    → ack 丢失 / 进程崩溃时, 同一 logical payment 可被再次授权 = 双重支付
    → _v2_replay 是纯内存 dict, 重启即清, 不能作为 production-grade 防线

目标 invariant (已在 issue #2 评论中对外确认):
    同一个 logical payment = 最多一次外部执行 + 最终最多一次本地 booking

本文件 = Step 1 failing tests (TDD 红):
    - 现在跑: 全部 RED (durable intent 层尚未实现)
    - SQLite reference backend 落地后逐个转绿

验收用例映射:
    acceptance 1 → test_ack_lost_restart_replay_one_charge_one_booking
    acceptance 2 → test_timeout_before_commit_reconcile_then_retry_one_charge
    acceptance 3 → test_same_key_same_resource_different_amount_conflict
    cv-scvd 修正 → test_same_key_different_resource_independent_intent
    acceptance 4 → test_two_processes_race_same_key_single_intent
    guardrail #4 → test_replay_succeeded_is_idempotent (regression: rebuild(rebuild) == rebuild)

IntentStore 契约 (Felix 2026-09-04 定, 详见 docs/ 设计讨论):
    store = SqliteIntentStore(path)
    intent = store.create_or_get(agent, to, idem_key, amount, fingerprint)
        # scope = (agent, to, idem_key)   [cv-scvd: payer, resource, key]
        # 同 scope 同 fingerprint → 返回已有 intent (幂等)
        # 同 scope 不同 fingerprint → 抛 IntentConflict
        # 返回 dict: {id, status, provider_key, ...}
    store.reserve(intent_id)        # 发送前原子 claim (防两进程同时发)
    store.mark_in_flight(intent_id) # RESERVED → IN_FLIGHT (CAS)
    store.mark_succeeded(intent_id) # IN_FLIGHT → SUCCEEDED (ack 确认)
    store.mark_unknown(intent_id)   # 超时/崩溃 → UNKNOWN
    store.reconcile(intent_id, outcome, proof)
        # UNKNOWN → SUCCEEDED (reconcile 证明已提交, 带 proof)
        # UNKNOWN → RECONCILED_FAILED (证明未发生)  ← 此后同 key 可重试
        # UNKNOWN 只能由 reconcile 或显式 expiry 解决; 调用方重试循环无权推进
    store.consume_once(intent_id)   # 恰好一次返回 True (book 闸门); 其余 False

    状态机:
        RESERVED → IN_FLIGHT → SUCCEEDED
                        ↘ UNKNOWN → (RECONCILIATION) → SUCCEEDED / RECONCILED_FAILED
    关键 invariant: UNKNOWN ≠ retry allowed

⚠️ 本文件引用的 intent 层 API 收敛在 _layer() 一处; 设计讨论如有变更只改那里。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from spendshield import SpendShield


# ─────────────────────────────────────────────────────────────────────
# 未来 API 契约 (单一收口处, 设计讨论后统一改这里)
# ─────────────────────────────────────────────────────────────────────
def _layer():
    """返回 SqliteIntentStore 类。尚未实现 → 显式 RED。"""
    try:
        from spendshield.intent import SqliteIntentStore  # type: ignore
        return SqliteIntentStore
    except Exception as e:  # ImportError etc.
        pytest.fail(f"[RED] durable intent 层未实现 (issue #2 P0): {e!r}")


def _fingerprint(recipient, amount, currency="USD", resource="", payment_method="x402"):
    return {"recipient": recipient, "amount": amount, "currency": currency,
            "resource": resource or recipient, "payment_method": payment_method}


def _guard():
    """测试用 guard: 只用于「本地 booking 计数」(guard.spent / book)。"""
    return SpendShield(dry_run=False, budget=1000.0, approve_new_recipient=False)


class _FakeRail:
    """模拟外部支付 rail: 按 provider_key 幂等(已执行的 key 不二次扣款)。

    真实 x402/provider 侧同 key 幂等; 我们的 bug = 重启后丢失 provider_key,
    重试生成新 key → rail 视为新支付 → 双重扣款。
    """

    def __init__(self):
        self.executions = []  # [{key, amount, to, committed}]

    def pay(self, provider_key, amount, to):
        for ex in self.executions:
            if ex["key"] == provider_key:
                return ex  # provider 侧幂等: 同 key 直接返回原结果
        ex = {"key": provider_key, "amount": amount, "to": to, "committed": True}
        self.executions.append(ex)
        return ex

    def lookup(self, provider_key):
        for ex in self.executions:
            if ex["key"] == provider_key:
                return ex
        return None


def _book_once(store, guard, intent_id):
    """consume_once 闸门: 只有首次 consume 成功才 book (durable exactly-once booking)。"""
    if store.consume_once(intent_id):
        intent = store.get(intent_id)
        guard.book(agent=intent["agent"], amount=intent["amount"], to=intent["to"])
        return True
    return False


# ─────────────────────────────────────────────────────────────────────
# acceptance 1: commit → ack 丢失 → restart → replay ⇒ 一次执行 + 一次 booking
# ─────────────────────────────────────────────────────────────────────
def test_ack_lost_restart_replay_one_charge_one_booking(tmp_path):
    """进程 A 支付成功但 ack 丢失; 重启后同一逻辑支付重试 →
    不得产生第二次外部执行; 最终恰好一次本地 booking。"""
    SqliteIntentStore = _layer()
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()
    fp = _fingerprint("api.example.com", 10.0)

    # ── 进程 A: create → reserve → in_flight → 外部执行; ack 丢失 → 崩 ──
    store_a = SqliteIntentStore(db)
    intent = store_a.create_or_get(agent="bot", to="api.example.com",
                                   idem_key="K-001", amount=10.0, fingerprint=fp)
    assert intent["status"] == "RESERVED"
    assert store_a.reserve(intent["id"]) is True
    assert store_a.mark_in_flight(intent["id"]) is True
    rail.pay(intent["provider_key"], 10.0, "api.example.com")   # 外部已扣款
    # (ack 丢失 → 无人 mark_succeeded, intent 停在 IN_FLIGHT)

    # ── 进程 B: 重启, 同一逻辑支付重试 ──
    store_b = SqliteIntentStore(db)
    guard_b = _guard()
    again = store_b.create_or_get(agent="bot", to="api.example.com",
                                  idem_key="K-001", amount=10.0, fingerprint=fp)
    # 期望: 命中已有 intent, 复用同一把 provider_key → 绝不新建外部支付
    assert again["id"] == intent["id"], "重试必须命中已有 intent, 不能新建"
    assert again["provider_key"] == intent["provider_key"], "必须复用同一把 provider key"

    # 重启方必须先 reconciliation 再决定, 不能直接重发
    assert store_b.mark_in_flight(again["id"]) is False, "已 IN_FLIGHT, 不能二次发送"
    assert rail.lookup(again["provider_key"]) is not None, "rail 应已记录该支付"
    assert store_b.reconcile(again["id"], outcome="SUCCEEDED",
                             proof="rail:committed:K-001") is True
    assert _book_once(store_b, guard_b, again["id"]) is True
    assert _book_once(store_b, guard_b, again["id"]) is False, "booking 必须恰好一次"

    assert len(rail.executions) == 1, "外部执行必须恰好一次(双重支付 = bug)"
    assert guard_b.spent == 10.0, "本地 booking 必须恰好一次(10.0, 不是 20.0)"


# ─────────────────────────────────────────────────────────────────────
# acceptance 2: 提交前超时 → reconciliation 证明未发生 → 同 key 重试 ⇒ 一次执行
# ─────────────────────────────────────────────────────────────────────
def test_timeout_before_commit_reconcile_then_retry_one_charge(tmp_path):
    """超时发生在 rail 提交之前 → reconciliation 证明无执行 →
    释放预留 + 同 key 重试成功; 全程外部执行一次、booking 一次。"""
    SqliteIntentStore = _layer()
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()
    fp = _fingerprint("api.example.com", 5.0)

    store = SqliteIntentStore(db)
    intent = store.create_or_get(agent="bot", to="api.example.com",
                                 idem_key="K-002", amount=5.0, fingerprint=fp)
    store.mark_in_flight(intent["id"])
    # 发送后超时 (rail 从未收到 / 未提交)
    assert rail.lookup(intent["provider_key"]) is None
    assert store.mark_unknown(intent["id"]) is True, "超时 → UNKNOWN"

    # UNKNOWN 绝不能被调用方重试循环自行推进 (第二个人强调的重点)
    with pytest.raises(Exception):
        store.mark_succeeded(intent["id"])  # 无 reconciliation 凭证不能直接成功
    # 重试撞上来: 还是同一个 intent, 不能新建
    again = store.create_or_get(agent="bot", to="api.example.com",
                                idem_key="K-002", amount=5.0, fingerprint=fp)
    assert again["id"] == intent["id"] and again["status"] == "UNKNOWN"
    assert store.mark_in_flight(again["id"]) is False, "UNKNOWN 不允许直接重发"

    # reconciliation 证明未发生 → RECONCILED_FAILED → 释放, 同 key 可重试
    assert store.reconcile(intent["id"], outcome="RECONCILED_FAILED",
                           proof="rail:absent:K-002") is True
    retry = store.create_or_get(agent="bot", to="api.example.com",
                                idem_key="K-002", amount=5.0, fingerprint=fp)
    assert retry["id"] != intent["id"], "证明失败后可新建 intent"
    store.mark_in_flight(retry["id"])
    rail.pay(retry["provider_key"], 5.0, "api.example.com")
    assert store.reconcile(retry["id"], outcome="SUCCEEDED",
                           proof="rail:committed:K-002") is True

    assert len(rail.executions) == 1, "外部执行必须恰好一次"
    assert rail.executions[0]["amount"] == 5.0


# ─────────────────────────────────────────────────────────────────────
# acceptance 3: 同 key + 同资源 + 不同金额 ⇒ CONFLICT
# ─────────────────────────────────────────────────────────────────────
def test_same_key_same_resource_different_amount_conflict(tmp_path):
    SqliteIntentStore = _layer()
    store = SqliteIntentStore(str(tmp_path / "intents.db"))
    store.create_or_get(agent="bot", to="api.example.com", idem_key="K-003",
                        amount=10.0, fingerprint=_fingerprint("api.example.com", 10.0))
    with pytest.raises(Exception, match="conflict|Conflict"):
        store.create_or_get(agent="bot", to="api.example.com", idem_key="K-003",
                            amount=99.0, fingerprint=_fingerprint("api.example.com", 99.0))


# ─────────────────────────────────────────────────────────────────────
# cv-scvd 修正: key scope = (payer, resource, key); 同 key 跨资源 = 独立 intent
# ─────────────────────────────────────────────────────────────────────
def test_same_key_different_resource_independent_intent(tmp_path):
    SqliteIntentStore = _layer()
    store = SqliteIntentStore(str(tmp_path / "intents.db"))
    a = store.create_or_get(agent="bot", to="api-a.example.com", idem_key="K-004",
                            amount=10.0, fingerprint=_fingerprint("api-a.example.com", 10.0))
    b = store.create_or_get(agent="bot", to="api-b.example.com", idem_key="K-004",
                            amount=10.0, fingerprint=_fingerprint("api-b.example.com", 10.0))
    # 同 key 打到不同资源 = 两个独立 intent, 而不是把 a 的错误结果缓存给 b
    assert a["id"] != b["id"]
    assert a["provider_key"] != b["provider_key"]


# ─────────────────────────────────────────────────────────────────────
# acceptance 4: 两进程抢同一 key ⇒ 一个 intent / 一次外部执行
# ─────────────────────────────────────────────────────────────────────
def test_two_processes_race_same_key_single_intent(tmp_path):
    SqliteIntentStore = _layer()
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()
    fp = _fingerprint("api.example.com", 8.0)

    # 两进程几乎同时 create_or_get (同一 durable db)
    store_a, store_b = SqliteIntentStore(db), SqliteIntentStore(db)
    ia = store_a.create_or_get(agent="bot", to="api.example.com",
                               idem_key="K-005", amount=8.0, fingerprint=fp)
    ib = store_b.create_or_get(agent="bot", to="api.example.com",
                               idem_key="K-005", amount=8.0, fingerprint=fp)
    assert ia["id"] == ib["id"], "并发抢同 key 必须收敛到同一 intent"

    # 两进程都想发: reserve 都过, 但 mark_in_flight 只有一个赢 (CAS)
    assert store_a.reserve(ia["id"]) is True
    assert store_b.reserve(ib["id"]) is True
    assert store_a.mark_in_flight(ia["id"]) is True
    assert store_b.mark_in_flight(ib["id"]) is False, "同 key 只能有一个发送方"

    rail.pay(ia["provider_key"], 8.0, "api.example.com")
    assert len(rail.executions) == 1, "两进程同 key → 外部执行只能一次"


# ─────────────────────────────────────────────────────────────────────
# guardrail #4 (Felix): startup rebuild 可重复 —— rebuild(rebuild(state)) == rebuild(state)
# ─────────────────────────────────────────────────────────────────────
def test_replay_succeeded_is_idempotent(tmp_path):
    """重启重放必须幂等: 跑 N 次结果一致; 每个 SUCCEEDED intent 的 booking 恰好一行。"""
    SqliteIntentStore = _layer()
    db = str(tmp_path / "intents.db")
    rail = _FakeRail()
    fp = _fingerprint("api.example.com", 3.0)

    store = SqliteIntentStore(db)
    it = store.create_or_get(agent="bot", to="api.example.com",
                             idem_key="K-006", amount=3.0, fingerprint=fp)
    store.mark_in_flight(it["id"])
    rail.pay(it["provider_key"], 3.0, "api.example.com")
    assert store.reconcile(it["id"], outcome="SUCCEEDED",
                           proof="rail:committed:K-006") is True

    # 第一次重建: 补上 booking
    new1, total1 = store.replay_succeeded()
    assert (new1, total1) == (1, 1), "首次重建应补 1 笔 booking"
    # 第二次、第三次重建: 全部 no-op, 结果不变 (rebuild(rebuild(state)) == rebuild(state))
    assert store.replay_succeeded() == (0, 1)
    assert store.replay_succeeded() == (0, 1)
    # DB 层 booking 恰好一行
    assert store.get(it["id"])["booked"] is True
    # 重建不产生第二行 (consume_once 依然只有一次 True)
    assert store.consume_once(it["id"]) is False
