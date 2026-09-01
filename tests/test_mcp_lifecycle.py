# -*- coding: utf-8 -*-
"""MCP Policy Lifecycle 端到端: 通过 MCP 工具走完 create→apply→rollback"""
import sys, os, tempfile, yaml, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from spendshield import SpendShield
from spendshield.mcp_server import SpendShieldMCP

BASE = {
    "version": "2.0.0",
    "policy": {"budget": {"daily": 100}, "transaction": {"max": 50},
               "merchants": {"allowed": ["amazon.com"], "blocked": []},
               "approval": {"over": 30, "new_merchant": False, "channel": "tg"}},
}


def _mcp():
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(BASE, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    return SpendShieldMCP(g)


def _good(version="2.1.0"):
    return {"version": version,
            "policy": {"budget": {"daily": 200}, "transaction": {"max": 80},
                       "merchants": {"allowed": ["amazon.com", "walmart.com"], "blocked": []},
                       "approval": {"over": 50, "new_merchant": True, "channel": "tg"}}}


def test_mcp_full_lifecycle():
    m = _mcp()
    # create
    r = m._lifecycle_create({"name": "v2.1", "policy": json.dumps(_good()), "by": "dev"})
    assert r["ok"] is True
    did = r["draft_id"]
    # stage(validate+simulate+scan)
    s = m._lifecycle_stage({"draft_id": did, "by": "dev"})
    assert s["ok"] is True, s
    assert s["simulation"][0]["decision"] == "ALLOW"
    assert s["stage"] == "scan"
    # 未 review 直接 apply → 必须失败
    a = m._lifecycle_apply({"draft_id": did, "by": "dev"})
    assert a["ok"] is False and "REVIEWED" in a["reason"]
    # review 必须有人
    rv = m._lifecycle_review({"draft_id": did, "by": ""})
    assert rv["ok"] is False
    rv = m._lifecycle_review({"draft_id": did, "by": "boss"})
    assert rv["ok"] is True
    # apply
    a = m._lifecycle_apply({"draft_id": did, "by": "boss"})
    assert a["ok"] is True and a["version"] == "2.1.0"
    assert m.guard._v2_policy.version == "2.1.0"
    # 生产生效
    rr = m._authorize_v2({"agent": "", "amount": 40, "to": "amazon.com"})
    assert rr["decision"] == "ALLOW"
    rr2 = m._authorize_v2({"agent": "", "amount": 60, "to": "amazon.com"})
    assert rr2["decision"] == "APPROVAL"
    # versions
    vs = m._dispatch("policy_versions", {})
    assert vs["ok"] is True and len(vs["versions"]) >= 2


def test_mcp_rollback():
    m = _mcp()
    r = m._lifecycle_create({"name": "v2.1", "policy": json.dumps(_good()), "by": "dev"})
    did = r["draft_id"]
    m._lifecycle_stage({"draft_id": did, "by": "dev"})
    m._lifecycle_review({"draft_id": did, "by": "boss"})
    m._lifecycle_apply({"draft_id": did, "by": "boss"})
    rb = m._lifecycle_rollback({"version": "2.0.0", "by": "boss"})
    assert rb["ok"] is True
    assert m.guard._v2_policy.version == "2.0.0"
    assert m._authorize_v2({"agent": "", "amount": 60, "to": "amazon.com"})["decision"] == "DENY"


def test_mcp_stage_blocker_detected():
    """scan 发现 UNLIMITED_SPEND → stage 返回 ok=False, 无法 review"""
    m = _mcp()
    r = m._lifecycle_create({"name": "wild", "policy": json.dumps({"version": "9.9", "policy": {}}), "by": "dev"})
    did = r["draft_id"]
    s = m._lifecycle_stage({"draft_id": did, "by": "dev"})
    assert s["ok"] is False
    codes = [f["code"] for f in s["findings"]]
    assert "UNLIMITED_SPEND" in codes
    rv = m._lifecycle_review({"draft_id": did, "by": "boss"})
    assert rv["ok"] is False


def test_mcp_lifecycle_audited_in_chain():
    m = _mcp()
    r = m._lifecycle_create({"name": "v2.1", "policy": json.dumps(_good()), "by": "dev"})
    did = r["draft_id"]
    m._lifecycle_stage({"draft_id": did, "by": "dev"})
    m._lifecycle_review({"draft_id": did, "by": "boss"})
    m._lifecycle_apply({"draft_id": did, "by": "boss"})
    ok, msg = m.guard.audit.verify_chain()
    assert ok, msg
    actions = [e.action for e in m.guard.audit.events]
    assert "policy_create" in actions and "policy_review" in actions and "policy_apply" in actions


def test_tools_list_contains_lifecycle():
    m = _mcp()
    names = [t["name"] for t in m.tools_list()["tools"]]
    for n in ("policy_create", "policy_stage", "policy_review", "policy_lifecycle_apply",
              "policy_rollback", "policy_versions"):
        assert n in names, f"缺工具 {n}"
