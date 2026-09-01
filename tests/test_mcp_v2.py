# -*- coding: utf-8 -*-
"""MCP Server V2 工具测试(policy_apply / policy_sim / spend_authorize / approve / reject)"""
import sys, os, json, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from spendshield import SpendShield
from spendshield.mcp_server import SpendShieldMCP

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 100, "monthly": 1000},
        "transaction": {"max": 50},
        "merchants": {"allowed": ["amazon.com"]},
        "approval": {"over": 30, "new_merchant": True, "channel": "tg"},
    },
    "agents": {"bot": {"approval": {"over": 30, "new_merchant": True, "channel": "tg"}}},
}


def make_mcp():
    import tempfile, yaml
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(POLICY, f)
        path = f.name
    g = SpendShield(dry_run=False)
    g.load_policy(path)
    os.unlink(path)
    return SpendShieldMCP(g)


# ── spend_authorize 三态 ─────────────────────────────────
def test_authorize_allow():
    m = make_mcp()
    r = m._authorize_v2({"agent": "bot", "amount": 20, "to": "amazon.com"})
    assert r["ok"] is True and r["decision"] == "ALLOW"
    assert r["rules"] == []


def test_authorize_deny_with_rules():
    m = make_mcp()
    r = m._authorize_v2({"agent": "bot", "amount": 75, "to": "amazon.com"})
    assert r["ok"] is False and r["decision"] == "DENY"
    assert r["rules"][0]["rule"] == "max_transaction"
    assert "$75.00" in r["reason_text"]


def test_authorize_approval_flow():
    m = make_mcp()
    r = m._authorize_v2({"agent": "bot", "amount": 40, "to": "amazon.com"})
    assert r["decision"] == "APPROVAL"
    aid = r["approval_id"]
    assert aid
    a = m._approve_v2({"approval_id": aid, "by": "felix"})
    assert a["decision"] == "ALLOW"
    # 二次批准 → 拒绝
    a2 = m._approve_v2({"approval_id": aid})
    assert a2["decision"] == "DENY"


def test_authorize_reject():
    m = make_mcp()
    r = m._authorize_v2({"agent": "bot", "amount": 40, "to": "amazon.com"})
    aid = r["approval_id"]
    rj = m._reject_v2({"approval_id": aid, "by": "felix"})
    assert rj["decision"] == "DENY"
    assert m.guard.spent == 0


# ── policy_sim(不花钱模拟) ───────────────────────────────
def test_policy_sim_single():
    m = make_mcp()
    r = m._policy_sim({"agent": "bot", "amount": 75, "to": "amazon.com"})
    assert r["ok"] is True
    assert r["results"]["75.0"]["decision"] == "DENY"
    assert m.guard.spent == 0   # 纯模拟不记账


def test_policy_sim_sweep():
    m = make_mcp()
    r = m._policy_sim({"agent": "bot", "amounts": [20, 30, 50, 51], "to": "amazon.com"})
    decisions = {float(k): v["decision"] for k, v in r["results"].items()}
    assert decisions[20.0] == "ALLOW"
    assert decisions[51.0] == "DENY"
    assert m.guard.spent == 0


# ── policy_apply(管理工具) ───────────────────────────────
def test_policy_apply_view_current():
    m = make_mcp()
    r = m._policy_apply({})
    assert r["ok"] is True
    assert r["version"] == "2.0.0"
    assert r["policy"]["budget"]["daily"] == 100


def test_policy_apply_new():
    m = make_mcp()
    new_policy = {"version": "2.1.0", "policy": {"transaction": {"max": 999}}}
    r = m._policy_apply({"policy": json.dumps(new_policy)})
    assert r["ok"] is True
    assert r["version"] == "2.1.0"
    # 新策略生效
    rr = m._authorize_v2({"agent": "bot", "amount": 100, "to": "amazon.com"})
    # 注意: 新策略没有 agents 配置, bot 未注册 → DENY
    assert rr["decision"] == "DENY"
    assert "unknown agent" in rr["reason_text"].lower()
    # 审计留痕(unknown-agent 拒绝也会写审计, 故用存在性断言)
    assert any(r.decision == "policy_applied" for r in m.guard.records)


def test_policy_apply_rejects_bad():
    m = make_mcp()
    bad = {"version": "x", "policy": {"transaction": {"max": -5}}}
    r = m._policy_apply({"policy": json.dumps(bad)})
    assert r["ok"] is False
    assert "rejected" in r["reason"]
    # 坏策略不生效: 原策略还在
    assert m.guard._v2_policy.version == "2.0.0"


# ── JSON-RPC 层 ──────────────────────────────────────────
def test_jsonrpc_dispatch():
    m = make_mcp()
    resp = m.tools_call("spend_authorize", {"agent": "bot", "amount": 75, "to": "amazon.com"})
    assert resp["isError"] is False
    body = json.loads(resp["content"][0]["text"])
    assert body["decision"] == "DENY"

    resp2 = m.tools_call("policy_sim", {"agent": "bot", "amount": 75, "to": "amazon.com"})
    body2 = json.loads(resp2["content"][0]["text"])
    assert body2["ok"] is True


def test_tools_list_contains_v2():
    m = make_mcp()
    names = [t["name"] for t in m.tools_list()["tools"]]
    for n in ("spend_authorize", "spend_approve", "spend_reject", "policy_sim", "policy_apply"):
        assert n in names
