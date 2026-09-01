# -*- coding: utf-8 -*-
"""
Security Suite ④ — double_spend
攻击: 同一笔交易(审批通过后)重复执行 / 二次批准 / 批准后再授权。
断言: 每笔钱只花一次。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from spendshield import SpendShield

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 1000},
        "transaction": {"max": 100},
        "merchants": {"allowed": ["amazon.com"]},
        "approval": {"over": 30, "new_merchant": False, "channel": "tg"},
    },
    "agents": {"spender": {"approval": {"over": 30, "new_merchant": False, "channel": "tg"}}},
}


def _make_guard():
    import tempfile, yaml, os
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(POLICY, f)
        path = f.name
    g = SpendShield(dry_run=False)
    g.load_policy(path)
    os.unlink(path)
    return g


def test_approve_twice_denied():
    g = _make_guard()
    r = g.authorize("spender", 40, "amazon.com")
    assert r.decision == "APPROVAL"
    a1 = g.approve(r.approval_id, by="felix")
    a2 = g.approve(r.approval_id, by="felix")   # 二次批准
    assert a1.decision == "ALLOW"
    assert a2.decision == "DENY"
    assert "unknown approval" in a2.reason.lower()
    assert g._v2_estate.spent_total == 40, "二次批准不得二次记账"


def test_approve_then_reject_after_approval():
    """批准后 reject 同一 id → 无效(已消费), 不影响已记账"""
    g = _make_guard()
    r = g.authorize("spender", 40, "amazon.com")
    g.approve(r.approval_id)
    rj = g.reject(r.approval_id)
    assert rj.decision == "DENY"
    assert g._v2_estate.spent_total == 40  # 已批准的账不变


def test_double_spend_with_idempotency():
    """审批批准后, 同 key 重授权 → 拒绝"""
    g = _make_guard()
    r = g.authorize("spender", 40, "amazon.com", meta={"idempotency_key": "tx-1"})
    g.approve(r.approval_id, by="felix")
    r2 = g.authorize("spender", 40, "amazon.com", meta={"idempotency_key": "tx-1"})
    assert r2.decision == "DENY"
    assert g._v2_estate.spent_total == 40


def test_pending_cleared_after_approve():
    g = _make_guard()
    r = g.authorize("spender", 40, "amazon.com")
    assert len(g.pending_approvals()) == 1
    g.approve(r.approval_id)
    assert g.pending_approvals() == []
