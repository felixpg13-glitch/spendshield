# -*- coding: utf-8 -*-
"""
发布前安全自查(2026-09-01) — 攻击者视角回归测试

自查发现并修复:
  1. [严重] 策略变更后旧审批残留: 宽松策略挂起的 APPROVAL, 收紧策略后仍可批准放行
  2. [中]   spend_reset 只重置旧层 _spent, V2 state 未重置 → 重置功能失效
  3. [中]   replay key 跨 agent 冲突: agent A 用 k1 会误伤 agent B 的 k1
  4. [中]   rate_hits 无 rate_limit 配置也无限增长(内存 DoS 面)
  5. [中]   approve 未批准前就把收款方标记可信(信任扩张)
  6. [低]   agents 配置篡改不受策略指纹保护
"""
import sys, os, tempfile, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from spendshield import SpendShield

LOOSE = {
    "version": "1.0",
    "policy": {"transaction": {"max": 100},
               "merchants": {"allowed": [], "blocked": []},
               "approval": {"over": 30, "new_merchant": False, "channel": "tg"}},
    "agents": {"bot": {"approval": {"over": 30, "new_merchant": False, "channel": "tg"}}},
}
TIGHT = {
    "version": "2.0",
    "policy": {"transaction": {"max": 10},
               "merchants": {"allowed": [], "blocked": []},
               "approval": {"over": 0, "new_merchant": False, "channel": ""}},
    "agents": {"bot": {"approval": {"over": 0, "new_merchant": False, "channel": ""}}},
}


def _guard(cfg):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    return g


# ── 1. 策略变更后旧审批必须作废 ──────────────────────────
def test_stale_approval_invalid_after_policy_change():
    g = _guard(LOOSE)
    r = g.authorize("bot", 40, "merchant-x")     # 宽松: >30 → APPROVAL
    assert r.decision == "APPROVAL"
    aid = r.approval_id
    g.load_policy.__self__._setup_v2(dict(TIGHT))   # 收紧到 max=10
    # 旧审批在新策略下必须不可用(或重新评估被拒)
    a = g.approve(aid, by="attacker")
    assert a.decision != "ALLOW", "策略收紧后旧审批仍放行 = 绕过!"
    assert g._v2_estate.spent_total == 0


def test_stale_approval_cleared_on_reload():
    """重新加载策略时, 挂起审批全部作废"""
    g = _guard(LOOSE)
    r = g.authorize("bot", 40, "merchant-x")
    assert r.decision == "APPROVAL"
    g.load_policy.__self__._setup_v2(dict(TIGHT))
    assert g.pending_approvals() == [], "策略变更后 pending 应清空"


# ── 2. spend_reset 必须完整重置 ──────────────────────────
def test_reset_clears_v2_state():
    g = _guard(LOOSE)
    g.authorize("bot", 10, "m1")
    g.authorize("bot", 10, "m1")
    assert g._v2_estate.spent_total == 20
    g.reset()   # 公开重置方法(替代旧的 _spent = 0 直改)
    assert g._v2_estate.spent_total == 0
    assert g._v2_estate.spent_daily == {}
    assert g._v2_estate.spent_monthly == {}
    assert g._v2_estate.spent_by_agent == {}
    assert g._spent == 0
    assert g._agent_spent == {}


def test_mcp_reset_calls_full_reset():
    from spendshield.mcp_server import SpendShieldMCP
    g = _guard(LOOSE)
    m = SpendShieldMCP(g)
    m._dispatch("spend_reset", {})
    assert g._v2_estate.spent_total == 0


# ── 3. replay key 跨 agent 隔离 ──────────────────────────
def test_replay_key_isolated_per_agent():
    g = _guard(LOOSE)
    g.register_agent("other-agent", budget=100)   # 第二个已注册 agent
    r1 = g.authorize("bot", 10, "m1", meta={"idempotency_key": "k1"})
    r2 = g.authorize("other-agent", 10, "m2", meta={"idempotency_key": "k1"})
    assert r1.decision == "ALLOW"
    assert r2.decision == "ALLOW", "不同 agent 同 key 不应互相误伤"
    # 同 agent 同 key 重放仍拦
    r3 = g.authorize("bot", 10, "m1", meta={"idempotency_key": "k1"})
    assert r3.decision == "DENY"


# ── 4. rate_hits 只在配置 rate_limit 时记录 ──────────────
def test_rate_hits_not_recorded_without_config():
    cfg = {"version": "1", "policy": {"transaction": {"max": 1000},
                                      "approval": {"over": 0, "new_merchant": False, "channel": ""}}}
    g = _guard(cfg)
    for i in range(5):
        g.authorize("", 1, f"shop-{i}")
    assert g._v2_estate.rate_hits == [], "没配 rate_limit 就不该记 rate_hits"


# ── 5. approve 只在批准成功时标记收款方可信 ──────────────
def test_approve_does_not_trust_when_denied():
    g = _guard(LOOSE)
    # 先花到预算边缘, 让 approve 时重新评估 DENY
    g._v2_estate.spent_total = 999999  # 模拟预算已尽(构造 LOOSE 无预算, 用 max 卡)
    # LOOSE max=100, 40 的审批批准后仍 ≤100 → ALLOW。改用 TIGHT 场景:
    g2 = _guard(LOOSE)
    r = g2.authorize("bot", 40, "merchant-new")
    assert r.decision == "APPROVAL"
    # 篡改 pending 金额到超限, 批准 → DENY, 收款方不应被信任
    g2._v2_estate.pending[r.approval_id].amount = 500
    a = g2.approve(r.approval_id, by="x")
    assert a.decision != "ALLOW"
    assert "merchant-new" not in g2._v2_estate.known_recipients, "被拒的审批不应扩张信任"


# ── 6. agents 配置纳入篡改保护 ───────────────────────────
def test_agents_tamper_detected():
    g = _guard(LOOSE)
    assert g.authorize("bot", 10, "m1").decision == "ALLOW"
    # 攻击: 改 agents 配置把自己的审批阈值放开
    g._v2_agents["bot"]["approval"]["over"] = 100000
    r = g.authorize("bot", 500, "m1")
    assert r.decision == "DENY", "agents 配置篡改应被指纹检测拦截"
    assert "tampered" in r.reason.lower()
