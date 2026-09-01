# -*- coding: utf-8 -*-
"""V2 可审计性: 每次资金决策可重现、可解释(0.7.1)"""
import sys, os, tempfile, yaml, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from spendshield import SpendShield

POLICY = {
    "version": "2.0.0",
    "policy": {"budget": {"daily": 100}, "transaction": {"max": 50},
               "merchants": {"allowed": ["amazon.com"], "blocked": []},
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


def test_audit_record_reproducible():
    """审计记录可重现: 能回答「这笔为什么被拒/放行」"""
    g = _guard()
    g.authorize("bot", 75, "amazon.com")          # DENY
    g.authorize("bot", 20, "amazon.com")          # ALLOW
    g.authorize("bot", 40, "amazon.com")          # APPROVAL
    deny = [r for r in g.records if r.decision == "v2_deny"][-1]
    allow = [r for r in g.records if r.decision == "v2_allow"][-1]
    appr = [r for r in g.records if r.decision == "v2_approval"][-1]

    # 决策字段
    assert deny.reason and "exceeds" in deny.reason
    # 可重现性字段
    for rec in (deny, allow, appr):
        assert rec.policy_version == "2.0.0"
        assert rec.engine_version          # 引擎版本非空
        assert rec.input_hash              # 请求指纹非空
        assert len(rec.input_hash) == 16
    # 相同请求 → 相同指纹(可复现)
    g2 = _guard()
    g2.authorize("bot", 75, "amazon.com")
    deny2 = [r for r in g2.records if r.decision == "v2_deny"][-1]
    assert deny.input_hash == deny2.input_hash, "同请求指纹应一致(可复现)"


def test_export_audit_contains_reproducibility():
    g = _guard()
    g.authorize("bot", 75, "amazon.com")
    path = g.export_audit("/tmp/ss_audit_repro.json")
    data = json.load(open(path, encoding="utf-8"))
    rec = data[-1]
    assert rec["policy_version"] == "2.0.0"
    assert rec["engine_version"]
    assert rec["input_hash"]
    os.unlink(path)


def test_audit_decision_matches_state():
    """审计不撒谎: 记录数与决策数一致, 决策类型可枚举"""
    g = _guard()
    for _ in range(10):
        g.authorize("bot", 20, "amazon.com")
    allows = [r for r in g.records if r.decision == "v2_allow"]
    denies = [r for r in g.records if r.decision == "v2_deny"]
    assert len(allows) == 5, "日预算 100, 20×5 后应拒绝"
    assert len(denies) == 5
    assert g._v2_estate.spent_total == 100
    assert sum(r.amount for r in allows) == 100   # 审计金额 = 实际记账
