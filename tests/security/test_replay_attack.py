# -*- coding: utf-8 -*-
"""
Security Suite ③ — replay_attack
攻击: 同一 idempotency_key 的请求重复提交, 试图重复扣款。
断言: 已成功的 key 重放 → 拒绝。
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
        "approval": {"over": 0, "new_merchant": False, "channel": ""},
    },
    "agents": {"bot": {"approval": {"over": 0, "new_merchant": False}}},
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


def test_replay_same_key_denied():
    g = _make_guard()
    meta = {"idempotency_key": "order-001", "order_id": "x1"}
    r1 = g.authorize("bot", 50, "amazon.com", meta=meta)
    r2 = g.authorize("bot", 50, "amazon.com", meta=meta)   # 重放
    assert r1.decision == "ALLOW"
    assert r2.decision == "DENY"
    assert "replay" in r2.reason.lower()
    assert g._v2_estate.spent_total == 50, "重放不应二次记账"


def test_replay_after_approval_denied():
    """审批流: 批准后同一 key 重放 → 拒绝(防 double spend)"""
    g = _make_guard()
    g._v2_agents["bot"]["approval"] = {"over": 30, "new_merchant": False, "channel": "tg"}
    meta = {"idempotency_key": "order-002"}
    r1 = g.authorize("bot", 40, "amazon.com", meta=meta)
    assert r1.decision == "APPROVAL"
    g.approve(r1.approval_id, by="felix")
    r2 = g.authorize("bot", 40, "amazon.com", meta=meta)
    assert r2.decision == "DENY"
    assert "replay" in r2.reason.lower()


def test_same_request_different_key_allowed():
    """不同 key 相同请求 → 视为新交易(合法重试/新单)"""
    g = _make_guard()
    r1 = g.authorize("bot", 50, "amazon.com", meta={"idempotency_key": "a"})
    r2 = g.authorize("bot", 50, "amazon.com", meta={"idempotency_key": "b"})
    assert r1.decision == "ALLOW"
    assert r2.decision == "ALLOW"


def test_replay_different_amount_same_key_denied():
    """同 key 改金额 → 也是重放(拒绝, 防篡改重放)"""
    g = _make_guard()
    r1 = g.authorize("bot", 50, "amazon.com", meta={"idempotency_key": "k1"})
    r2 = g.authorize("bot", 80, "amazon.com", meta={"idempotency_key": "k1"})
    assert r1.decision == "ALLOW"
    assert r2.decision == "DENY"
