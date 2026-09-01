# -*- coding: utf-8 -*-
"""SpendShield V2 Policy Engine — 核心单元测试"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from spendshield.policy import (
    EngineState, PaymentRequest, PolicySimulator, load_policy,
    PolicyValidationError, evaluate, snapshot, list_versions, diff, load_version,
)
from spendshield.policy.schema import AgentPolicy

SHOPPING = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 100, "monthly": 1000},
        "transaction": {"max": 50, "min": 0.01},
        "merchants": {"allowed": ["amazon.com", "walmart.com"], "allow_subdomains": True},
        "approval": {"over": 30, "new_merchant": True, "channel": "tg"},
        "rate_limit": {"window_s": 3600, "max_calls": 5},
    },
    "agents": {
        "shopping-agent": {
            "transaction": {"max": 50},
            "approval": {"over": 30, "new_merchant": True},
        }
    },
}


def make_sim():
    return PolicySimulator(policy_raw=SHOPPING)


# ── 基础决策 ──────────────────────────────────────────────
def test_allow_small_known_merchant():
    sim = make_sim()
    r = sim.evaluate("shopping-agent", 20, "amazon.com")
    assert r.decision == "ALLOW"
    assert "approved" in r.reason


def test_deny_over_max_transaction_with_explanation():
    sim = make_sim()
    r = sim.evaluate("shopping-agent", 75, "amazon.com")
    assert r.decision == "DENY"
    assert r.rules[0].rule == "max_transaction"
    assert "$75.00" in r.reason and "$50.00" in r.reason
    assert "exceeds" in r.reason.lower()


def test_deny_merchant_not_allowed():
    sim = make_sim()
    r = sim.evaluate("shopping-agent", 10, "evil-shop.example.com")
    assert r.decision == "DENY"
    assert r.rules[0].rule == "merchant_allowed"


def test_deny_blacklist():
    cfg = dict(SHOPPING)
    cfg["policy"] = dict(SHOPPING["policy"], merchants={"blocked": ["scam.com"], "allowed": []})
    sim = PolicySimulator(policy_raw=cfg)
    r = sim.evaluate("shopping-agent", 10, "scam.com")
    assert r.decision == "DENY"
    assert r.rules[0].rule == "merchant_blocked"


def test_deny_non_positive_amount():
    sim = make_sim()
    assert sim.evaluate("shopping-agent", 0, "amazon.com").decision == "DENY"
    assert sim.evaluate("shopping-agent", -5, "amazon.com").decision == "DENY"


def test_deny_unknown_agent_safe_default():
    # 未知 agent 由 guard 层拒绝; 引擎层面: 未注册 agent 用全局策略
    # (allow_unknown=False 时 guard 应直接 DENY, 引擎测试这里验证全局策略生效)
    sim = make_sim()
    r = sim.evaluate("unknown-agent", 75, "amazon.com")
    assert r.decision == "DENY"  # 全局 transaction.max=50 兜住


# ── 预算(含累计) ──────────────────────────────────────────
def test_daily_budget_exceeded():
    sim = make_sim()
    # 30 ≤ max 50, 30 不 > over 30 → 不触发审批; 100 日预算
    assert sim.evaluate("shopping-agent", 30, "amazon.com").decision == "ALLOW"
    assert sim.evaluate("shopping-agent", 30, "amazon.com").decision == "ALLOW"   # 60 ≤ 100
    assert sim.evaluate("shopping-agent", 30, "amazon.com").decision == "ALLOW"   # 90 ≤ 100
    r = sim.evaluate("shopping-agent", 30, "amazon.com")
    assert r.decision == "DENY"   # 120 > 100
    assert r.rules[0].rule == "daily_budget"


def test_allow_exact_budget_boundary():
    sim = make_sim()
    assert sim.evaluate("shopping-agent", 30, "amazon.com").decision == "ALLOW"
    assert sim.evaluate("shopping-agent", 30, "amazon.com").decision == "ALLOW"  # 60 ≤ 100
    assert sim.evaluate("shopping-agent", 30, "amazon.com").decision == "ALLOW"  # 90 ≤ 100
    assert sim.evaluate("shopping-agent", 30, "amazon.com").decision == "DENY"   # 120 > 100


def test_budget_resets_on_new_day():
    sim = make_sim()
    sim.state.spent_daily["2099-01-01"] = 99.0  # 模拟"昨天"已花
    assert sim.evaluate("shopping-agent", 30, "amazon.com").decision == "ALLOW"  # 今天从 0 算


# ── 审批 ──────────────────────────────────────────────────
def test_approval_over_threshold():
    sim = make_sim()
    r = sim.evaluate("shopping-agent", 40, "amazon.com")   # > 30 且 ≤50
    assert r.decision == "APPROVAL"
    assert r.approval_id
    assert r.rules[0].rule == "approval_required"


def test_approval_new_merchant():
    # 无白名单模式: 新商户首次交易 → APPROVAL
    cfg = dict(SHOPPING)
    cfg["policy"] = dict(SHOPPING["policy"], merchants={"allowed": [], "blocked": []})
    sim = PolicySimulator(policy_raw=cfg)
    r = sim.evaluate("shopping-agent", 20, "newexpress.com")
    assert r.decision == "APPROVAL"
    assert r.rules[0].rule == "approval_required"


def test_deny_when_no_approval_channel():
    cfg = dict(SHOPPING)
    cfg["policy"] = dict(SHOPPING["policy"], approval={"over": 30, "new_merchant": True, "channel": ""})
    sim = PolicySimulator(policy_raw=cfg)
    r = sim.evaluate("shopping-agent", 40, "amazon.com")
    assert r.decision == "DENY"  # 无通道 → 安全默认拒绝
    assert "no approval channel" in r.reason.lower()


def test_known_recipient_skips_new_merchant_approval():
    cfg = dict(SHOPPING)
    cfg["policy"] = dict(SHOPPING["policy"], merchants={"allowed": [], "blocked": []})
    sim = PolicySimulator(policy_raw=cfg)
    sim.state.known_recipients.add("newexpress.com")
    r = sim.evaluate("shopping-agent", 20, "newexpress.com")
    assert r.decision == "ALLOW"  # 已认识 → 不再要求审批


# ── 频率限制 ──────────────────────────────────────────────
def test_rate_limit_calls():
    sim = make_sim()
    for _ in range(5):
        assert sim.evaluate("shopping-agent", 10, "amazon.com").decision == "ALLOW"
    r = sim.evaluate("shopping-agent", 10, "amazon.com")
    assert r.decision == "DENY"
    assert r.rules[0].rule == "rate_limit_calls"


def test_rate_limit_per_agent_merchant():
    sim = make_sim()
    for _ in range(5):
        sim.evaluate("shopping-agent", 10, "amazon.com")
    # 换商户/换 agent 不受影响
    assert sim.evaluate("shopping-agent", 10, "walmart.com").decision == "ALLOW"


# ── agent 策略合并(CSS 式覆盖) ────────────────────────────
def test_agent_override_transaction_max():
    cfg = dict(SHOPPING)
    cfg["agents"] = {"big-spender": {"transaction": {"max": 200}, "approval": {"over": 1000, "new_merchant": False}}}
    sim = PolicySimulator(policy_raw=cfg)
    assert sim.evaluate("big-spender", 100, "amazon.com").decision == "ALLOW"
    assert sim.evaluate("big-spender", 250, "amazon.com").decision == "DENY"


# ── 校验器 ────────────────────────────────────────────────
def test_validator_rejects_bad_policy():
    with pytest.raises(PolicyValidationError):
        load_policy({"policy": {"transaction": {"max": -1}}})  # 缺 version + 负数
    with pytest.raises(PolicyValidationError):
        load_policy({"version": "1", "policy": {"approval": {"channel": "sms"}}})
    with pytest.raises(PolicyValidationError):
        load_policy({"version": "1", "policy": {"transaction": {"max": 1, "min": 5}}})


def test_validator_accepts_good_policy():
    p = load_policy(SHOPPING)
    assert p.version == "2.0.0"
    assert p.budget.daily == 100
    assert p.merchants.allowed == ["amazon.com", "walmart.com"]


# ── Simulator ─────────────────────────────────────────────
def test_sweep_finds_boundaries():
    sim = make_sim()
    out = sim.sweep("shopping-agent", "amazon.com", [20, 30, 40, 50, 51])
    assert out[20] == "ALLOW"
    assert out[40] == "APPROVAL"   # > 30 审批
    assert out[51] == "DENY"       # > 50 拒绝


def test_matrix_lists_rules():
    sim = make_sim()
    rules = sim.matrix("shopping-agent")
    ids = [r["rule"] for r in rules]
    assert "max_transaction" in ids
    assert "approval_required(over)" in ids


# ── 版本管理 ──────────────────────────────────────────────
def test_versioning(tmp_path):
    snap1 = snapshot(SHOPPING, base_dir=str(tmp_path))
    assert snap1.endswith("2.0.0.yaml")
    assert "2.0.0" in list_versions(base_dir=str(tmp_path))
    v2 = dict(SHOPPING, version="2.0.1", policy=dict(SHOPPING["policy"], transaction={"max": 100}))
    snapshot(v2, base_dir=str(tmp_path))
    d = diff("2.0.0", "2.0.1", base_dir=str(tmp_path))
    assert "max" in d
    rolled = load_version("2.0.0", base_dir=str(tmp_path))
    assert rolled["version"] == "2.0.0"


# ── 解释的可读性(产品差异点) ─────────────────────────────
def test_reason_is_human_readable():
    sim = make_sim()
    r = sim.evaluate("shopping-agent", 75, "amazon.com")
    text = str(r)
    assert "DENY" in text
    assert "Reason:" in text
    assert "max_transaction" in text


def test_allowed_shows_warning_near_budget():
    sim = make_sim()
    # 25 ≤ 30 不触发审批; 25×3=75 后累计 85 > 100*0.8 → warn
    sim.evaluate("shopping-agent", 25, "amazon.com")
    sim.evaluate("shopping-agent", 25, "amazon.com")
    sim.evaluate("shopping-agent", 25, "amazon.com")
    r = sim.evaluate("shopping-agent", 10, "amazon.com")
    assert r.decision == "ALLOW"
    assert any(h.rule == "daily_budget" and h.severity == "warn" for h in r.rules)
