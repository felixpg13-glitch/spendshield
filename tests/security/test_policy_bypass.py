# -*- coding: utf-8 -*-
"""
Security Suite ⑤ — policy_bypass
攻击: 空 agent / 未知 agent / 负金额 / 0 金额 / 巨大金额 / 非数字金额。
断言: 全部被安全默认拦截, 不产生任何记账。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from spendshield import SpendShield

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 1000},
        "transaction": {"max": 100},
        "merchants": {"allowed": ["amazon.com"]},
        "approval": {"over": 0, "new_merchant": False, "channel": ""},
    },
    "agents": {"known-agent": {"approval": {"over": 0, "new_merchant": False}}},
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


def test_unknown_agent_denied():
    g = _make_guard()
    r = g.authorize("stranger", 10, "amazon.com")
    assert r.decision == "DENY"
    assert "unknown agent" in r.reason.lower()
    assert g._v2_estate.spent_total == 0


def test_empty_agent_uses_global_policy():
    """空 agent(无身份): 走全局策略, 仍然受全局规则约束"""
    g = _make_guard()
    r = g.authorize("", 10, "amazon.com")
    assert r.decision == "ALLOW"   # 全局允许
    r2 = g.authorize("", 500, "amazon.com")
    assert r2.decision == "DENY"   # 全局 max=100 兜底


def test_negative_amount_denied():
    g = _make_guard()
    r = g.authorize("known-agent", -50, "amazon.com")
    assert r.decision == "DENY"
    assert g._v2_estate.spent_total == 0


def test_zero_amount_denied():
    g = _make_guard()
    r = g.authorize("known-agent", 0, "amazon.com")
    assert r.decision == "DENY"
    assert g._v2_estate.spent_total == 0


def test_huge_amount_denied():
    g = _make_guard()
    r = g.authorize("known-agent", 1e18, "amazon.com")
    assert r.decision == "DENY"
    assert g._v2_estate.spent_total == 0


def test_nan_inf_amount_denied():
    g = _make_guard()
    r = g.authorize("known-agent", float("nan"), "amazon.com")
    assert r.decision == "DENY"
    r2 = g.authorize("known-agent", float("inf"), "amazon.com")
    assert r2.decision == "DENY"


def test_empty_merchant_denied():
    """空收款方: 不在白名单 → 拒绝"""
    g = _make_guard()
    r = g.authorize("known-agent", 10, "")
    assert r.decision == "DENY"
