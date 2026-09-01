# -*- coding: utf-8 -*-
"""0.8 Policy Lifecycle: 状态机/扫描/评审/上线/回滚/审计"""
import sys, os, tempfile, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from spendshield import SpendShield
from spendshield.policy.lifecycle import PolicyManager, PolicyLifecycleError

BASE = {
    "version": "2.0.0",
    "policy": {"budget": {"daily": 100}, "transaction": {"max": 50},
               "merchants": {"allowed": ["amazon.com"], "blocked": []},
               "approval": {"over": 30, "new_merchant": False, "channel": "tg"}},
}


def _setup():
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(BASE, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    return g, PolicyManager(g)


def _good_policy(version="2.1.0"):
    return {"version": version,
            "policy": {"budget": {"daily": 200}, "transaction": {"max": 80},
                       "merchants": {"allowed": ["amazon.com", "walmart.com"], "blocked": []},
                       "approval": {"over": 50, "new_merchant": True, "channel": "tg"}}}


# ── 状态机: 不允许跳跃 ──────────────────────────────────
def test_cannot_apply_without_review():
    g, pm = _setup()
    d = pm.create("v2.1", _good_policy(), by="dev")
    with pytest.raises(PolicyLifecycleError) as e:
        pm.apply(d.id, by="dev")     # CREATED 直接 APPLY
    assert "REVIEWED" in str(e.value)


def test_full_lifecycle():
    g, pm = _setup()
    d = pm.create("v2.1", _good_policy(), by="dev")
    assert d.state == "CREATED"
    v = pm.validate(d.id, by="dev")
    assert v["ok"] is True
    s = pm.simulate(d.id, cases=[{"amount": 10, "to": "amazon.com"},
                                 {"amount": 1000, "to": "amazon.com"}], by="dev")
    assert s["ok"] is True
    decisions = [r["decision"] for r in s["results"]]
    assert decisions[0] == "ALLOW"
    sc = pm.scan(d.id, by="dev")
    assert sc["ok"] is True
    rv = pm.review(d.id, by="boss")
    assert rv["ok"] is True
    ap = pm.apply(d.id, by="boss")
    assert ap["ok"] is True and ap["version"] == "2.1.0"
    # 生产已切换
    assert g._v2_policy.version == "2.1.0"
    assert g.authorize("", 40, "amazon.com").decision == "ALLOW"   # ≤ over 50 且 ≤ max 80
    assert g.authorize("", 60, "amazon.com").decision == "APPROVAL"  # > over 50


def test_state_sequence_enforced():
    g, pm = _setup()
    d = pm.create("x", _good_policy(), by="dev")
    with pytest.raises(PolicyLifecycleError):
        pm.simulate(d.id, by="dev")     # 未 validate
    pm.validate(d.id, by="dev")
    with pytest.raises(PolicyLifecycleError):
        pm.review(d.id, by="boss")      # 未 simulate+scan
    pm.simulate(d.id, by="dev")
    with pytest.raises(PolicyLifecycleError):
        pm.apply(d.id, by="boss")       # 未 scan+review
    pm.scan(d.id, by="dev")
    with pytest.raises(PolicyLifecycleError):
        pm.apply(d.id, by="boss")       # 未 review
    pm.review(d.id, by="boss")
    assert pm.apply(d.id, by="boss")["ok"] is True


# ── SCAN 安全扫描 ────────────────────────────────────────
def test_scan_detects_unlimited_policy():
    g, pm = _setup()
    d = pm.create("wild", {"version": "9.9", "policy": {}}, by="dev")
    pm.validate(d.id, by="dev")
    pm.simulate(d.id, by="dev")
    sc = pm.scan(d.id, by="dev")
    assert sc["ok"] is False
    codes = [f["code"] for f in sc["findings"]]
    assert "UNLIMITED_SPEND" in codes


def test_scan_detects_rule_conflict():
    g, pm = _setup()
    p = _good_policy()
    p["policy"]["merchants"]["allowed"] = ["amazon.com", "scam.com"]
    p["policy"]["merchants"]["blocked"] = ["scam.com"]
    d = pm.create("conflict", p, by="dev")
    pm.validate(d.id, by="dev"); pm.simulate(d.id, by="dev")
    sc = pm.scan(d.id, by="dev")
    codes = [f["code"] for f in sc["findings"]]
    assert "RULE_CONFLICT" in codes


def test_scan_blocker_blocks_review():
    """blocker 级发现 → 不允许进入评审(必须修)"""
    g, pm = _setup()
    d = pm.create("wild", {"version": "9.9", "policy": {}}, by="dev")
    pm.validate(d.id, by="dev"); pm.simulate(d.id, by="dev")
    sc = pm.scan(d.id, by="dev")
    assert sc["ok"] is False
    # blocker 阻止状态前进 → review 被状态机自然阻断
    assert d.state == "SIMULATED"
    with pytest.raises(PolicyLifecycleError):
        pm.review(d.id, by="boss")


# ── Review 必须有人 ──────────────────────────────────────
def test_review_requires_approver():
    g, pm = _setup()
    d = pm.create("x", _good_policy(), by="dev")
    pm.validate(d.id, by="dev"); pm.simulate(d.id, by="dev"); pm.scan(d.id, by="dev")
    with pytest.raises(PolicyLifecycleError):
        pm.review(d.id, by="")
    assert pm.review(d.id, by="boss")["ok"] is True


# ── 回滚 ─────────────────────────────────────────────────
def test_rollback_to_previous_version():
    g, pm = _setup()
    d = pm.create("v2.1", _good_policy(), by="dev")
    pm.validate(d.id, by="dev"); pm.simulate(d.id, by="dev"); pm.scan(d.id, by="dev")
    pm.review(d.id, by="boss"); pm.apply(d.id, by="boss")
    assert g._v2_policy.version == "2.1.0"
    rb = pm.rollback("2.0.0", by="boss")
    assert rb["ok"] is True
    assert g._v2_policy.version == "2.0.0"
    assert g.authorize("", 60, "amazon.com").decision == "DENY"   # 回到 max=50


def test_rollback_unknown_version():
    g, pm = _setup()
    with pytest.raises(PolicyLifecycleError):
        pm.rollback("nope", by="boss")


# ── 审计留痕(哈希链) ────────────────────────────────────
def test_lifecycle_fully_audited():
    g, pm = _setup()
    d = pm.create("v2.1", _good_policy(), by="dev")
    pm.validate(d.id, by="dev"); pm.simulate(d.id, by="dev"); pm.scan(d.id, by="dev")
    pm.review(d.id, by="boss"); pm.apply(d.id, by="boss")
    actions = [e.action for e in g.audit.events]
    for a in ("policy_create", "policy_validate", "policy_simulate",
              "policy_scan", "policy_review", "policy_apply"):
        assert a in actions, f"缺审计: {a}"
    # 哈希链完整(生命周期事件也进链)
    ok, msg = g.audit.verify_chain()
    assert ok, msg
    # 可追溯: 谁在什么时候上线了哪个版本
    apply_ev = [e for e in g.audit.events if e.action == "policy_apply"][-1]
    assert "2.0.0" in apply_ev.primary_reason and "2.1.0" in apply_ev.primary_reason
    assert apply_ev.actor == "boss"


# ── 生产策略切换后生效 ───────────────────────────────────
def test_applied_policy_affects_authorize():
    g, pm = _setup()
    d = pm.create("v2.1", _good_policy(), by="dev")
    pm.validate(d.id, by="dev"); pm.simulate(d.id, by="dev"); pm.scan(d.id, by="dev")
    pm.review(d.id, by="boss"); pm.apply(d.id, by="boss")
    # 新策略: max=80, over=50, 白名单含 walmart
    assert g.authorize("", 40, "amazon.com").decision == "ALLOW"    # ≤50 且 ≤80
    assert g.authorize("", 70, "amazon.com").decision == "APPROVAL"  # >50 触发审批
    assert g.authorize("", 90, "amazon.com").decision == "DENY"     # >80
    assert g.authorize("", 10, "walmart.com").decision == "ALLOW"   # 白名单新增
    assert g.authorize("", 10, "evil.io").decision == "DENY"        # 非白名单
