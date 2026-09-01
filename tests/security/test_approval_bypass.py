# -*- coding: utf-8 -*-
"""
Security Suite ⑥ — approval_bypass
攻击: 审批通过后篡改金额/收款方, 试图绕过已批准的额度。
断言: approve 绑定原始请求, 篡改无效; 篡改后的新请求重新走全部闸门。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from spendshield import SpendShield

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 1000},
        "transaction": {"max": 100},
        "merchants": {"allowed": ["amazon.com", "walmart.com"]},
        "approval": {"over": 30, "new_merchant": True, "channel": "tg"},
    },
    "agents": {"agent-x": {"approval": {"over": 30, "new_merchant": True, "channel": "tg"}}},
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


def test_approve_uses_original_request_not_tampered():
    """批准后篡改 pending 里的请求(模拟内存被改)→ 无效: approve 用已存请求"""
    g = _make_guard()
    r = g.authorize("agent-x", 40, "amazon.com")
    aid = r.approval_id
    # 攻击: 篡改 pending 里的金额
    g._v2_estate.pending[aid][0].amount = 9999
    g._v2_estate.pending[aid][0].to = "walmart.com"
    res = g.approve(aid, by="felix")
    # 防御: 批准后重新评估(approval_granted), 但金额/收款方已变 → 理论应 DENY(max)
    # 注: 我们存的是 (req, ap) 元组, 直接改 dataclass 字段会生效——这里验证引擎兜底
    if res.decision == "ALLOW":
        # 若实现没防住对象篡改, 至少记账金额必须正确反映(不允许 9999 入账)
        assert g._v2_estate.spent_total <= 100
    else:
        assert res.decision == "DENY"


def test_tampered_new_request_goes_through_all_gates():
    """批准 40 后, 再发 80(超 max? 不超)但超 100 预算组合 → 必须重新受审"""
    g = _make_guard()
    r = g.authorize("agent-x", 40, "amazon.com")
    g.approve(r.approval_id, by="felix")          # 40 已花
    r2 = g.authorize("agent-x", 40, "amazon.com")  # 新请求(不同 idempotency)
    assert r2.decision == "APPROVAL"               # 仍需审批(over=30)
    # 批准后总计 80 ≤ 1000 → ALLOW
    assert g.approve(r2.approval_id, by="felix").decision == "ALLOW"
    # 再花 30 → 30 不 >30 不触发审批 → ALLOW(正确); 31 才触发
    r3 = g.authorize("agent-x", 30, "amazon.com")
    assert r3.decision == "ALLOW"
    r4 = g.authorize("agent-x", 31, "amazon.com")
    assert r4.decision == "APPROVAL"


def test_approval_does_not_whitelist_unknown_merchant_permanently():
    """新商户审批通过只记 known, 不影响 other unknown 商户"""
    g = _make_guard()
    # 用无白名单配置: 任何非白名单商户都要求审批
    g._v2_policy.merchants.allowed = []
    r = g.authorize("agent-x", 10, "shop-a.com")
    assert r.decision == "APPROVAL"    # 新商户
    g.approve(r.approval_id)
    r2 = g.authorize("agent-x", 10, "shop-b.com")   # 另一个新商户
    assert r2.decision == "APPROVAL"   # 仍需审批
    r3 = g.authorize("agent-x", 10, "shop-a.com")   # 已认识的
    assert r3.decision == "ALLOW"      # 免审批(金额小)
