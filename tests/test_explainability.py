# -*- coding: utf-8 -*-
"""Explainability: 稳定机器码 + 自然语言解释 + MCP 决策可解释"""
import sys, os, tempfile, yaml, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from spendshield import SpendShield
from spendshield.policy.explanation import explain, primary_rule, rule_code

POLICY = {
    "version": "2.0.0",
    "policy": {"budget": {"daily": 100}, "transaction": {"max": 50},
               "merchants": {"allowed": ["amazon.com"], "blocked": ["scam.com"]},
               "approval": {"over": 30, "new_merchant": False, "channel": "tg"}},
    "agents": {"bot": {"approval": {"over": 30, "new_merchant": False, "channel": "tg"}}},
}


def _guard():
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(POLICY, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    return g


def test_rule_codes_stable():
    """机器码稳定: 规则 → 唯一 code"""
    assert rule_code("max_transaction") == "MAX_TRANSACTION_EXCEEDED"
    assert rule_code("daily_budget") == "DAILY_BUDGET_EXCEEDED"
    assert rule_code("merchant_blocked") == "MERCHANT_BLOCKED"
    assert rule_code("approval_required") == "APPROVAL_REQUIRED"
    # 未登记规则也有兜底(不会崩)
    assert rule_code("future_rule") == "FUTURE_RULE"


def test_to_dict_contains_code():
    g = _guard()
    r = g.authorize("bot", 75, "amazon.com")
    assert r.decision == "DENY"
    d = r.to_dict()
    codes = [h["code"] for h in d["rules"]]
    assert "MAX_TRANSACTION_EXCEEDED" in codes
    assert "code" in d["rules"][0]


def test_explain_natural_language():
    g = _guard()
    r = g.authorize("bot", 75, "amazon.com")
    text = explain(r)
    assert "Decision: DENY" in text
    assert "MAX_TRANSACTION_EXCEEDED" in text
    assert "$75.00" in text and "$50.00" in text
    assert "Policy version: 2.0.0" in text


def test_primary_rule_priority():
    """多规则命中时主因按优先级: 黑名单 > 金额 > 预算"""
    g = _guard()
    # 黑名单商户 + 超限金额 → 主因 MERCHANT_BLOCKED
    r = g.authorize("bot", 9999, "scam.com")
    assert r.decision == "DENY"
    p = primary_rule(r.rules)
    assert p["code"] == "MERCHANT_BLOCKED"


def test_explain_approval_includes_id():
    g = _guard()
    r = g.authorize("bot", 40, "amazon.com")
    assert r.decision == "APPROVAL"
    text = explain(r)
    assert "APPROVAL_REQUIRED" in text
    assert r.approval_id in text


def test_mcp_authorize_returns_codes():
    """MCP 返回结构化 codes, Agent/LLM 可直接消费"""
    from spendshield.mcp_server import SpendShieldMCP
    g = _guard()
    m = SpendShieldMCP(g)
    out = m.tools_call("spend_authorize", {"agent": "bot", "amount": 75, "to": "amazon.com"})
    body = json.loads(out["content"][0]["text"])
    assert body["decision"] == "DENY"
    assert any(h["code"] == "MAX_TRANSACTION_EXCEEDED" for h in body["rules"])
