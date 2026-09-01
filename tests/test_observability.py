# -*- coding: utf-8 -*-
"""Observability: status() 运行状态快照 + MCP spend_status 增强"""
import sys, os, tempfile, yaml, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from spendshield import SpendShield

POLICY = {
    "version": "2.0.0",
    "policy": {"budget": {"daily": 100, "monthly": 500}, "transaction": {"max": 50},
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


def test_status_full_snapshot():
    g = _guard()
    g.authorize("bot", 40, "amazon.com")   # APPROVAL
    g.authorize("bot", 20, "amazon.com")   # ALLOW
    s = g.status()
    assert s["engine"] == "v2-policy-engine"
    assert s["engine_version"]
    assert s["policy_version"] == "2.0.0"
    assert s["spent"] == 20
    assert s["budget"]["daily"]["limit"] == 100
    assert s["budget"]["daily"]["used"] == 20
    assert s["budget"]["monthly"]["limit"] == 500
    assert s["pending_approvals"] == 1
    assert s["allowed"] == 1


def test_status_after_approval():
    g = _guard()
    r = g.authorize("bot", 40, "amazon.com")
    g.approve(r.approval_id, by="felix")
    s = g.status()
    assert s["spent"] == 40
    assert s["pending_approvals"] == 0
    assert s["spent_by_agent"]["bot"] == 40


def test_mcp_spend_status_returns_v2():
    from spendshield.mcp_server import SpendShieldMCP
    g = _guard()
    m = SpendShieldMCP(g)
    g.authorize("bot", 20, "amazon.com")
    out = m.tools_call("spend_status", {})
    body = json.loads(out["content"][0]["text"])
    assert body["engine"] == "v2-policy-engine"
    assert body["policy_version"] == "2.0.0"
    assert body["spent"] == 20
