# -*- coding: utf-8 -*-
"""
V2 Hardening ④ — Policy Engine 边界 / 规则冲突 / 默认策略

  1. 规则冲突: 黑名单 vs 白名单同时命中 → 黑名单(deny)优先
  2. 规则优先级: merchant → amount → budget → rate → approval(先命中的致命规则获胜)
  3. 默认策略(空 policy): 与文档一致 = 无限制; 但「显式空」≠「未加载」(未加载必须拒绝)
  4. agent 合并冲突: agent 白名单与全局黑名单合并(只更严)
  5. 边界值: 恰好等于阈值(<= / >= 语义)
"""
import sys, os, tempfile, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from spendshield import SpendShield

# ── 规则冲突 ─────────────────────────────────────────────
def test_blocklist_wins_over_allowlist():
    """同一商户同时在白名单和黑名单 → 黑名单优先(deny 优先于 allow)"""
    cfg = {"version": "1", "policy": {
        "transaction": {"max": 1000},
        "merchants": {"allowed": ["amazon.com"], "blocked": ["amazon.com"]},
        "approval": {"over": 0, "new_merchant": False, "channel": ""}}}
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg, f); f.close()
    g = SpendShield(dry_run=False); g.load_policy(f.name); os.unlink(f.name)
    r = g.authorize("", 10, "amazon.com")
    assert r.decision == "DENY"
    assert r.rules[0].rule == "merchant_blocked"


def test_agent_blocked_merges_with_global():
    """agent 配了黑名单 + 全局黑名单 → 合并(只更严)"""
    cfg = {"version": "1", "policy": {
        "transaction": {"max": 1000},
        "merchants": {"allowed": [], "blocked": ["global-scam.com"]},
        "approval": {"over": 0, "new_merchant": False, "channel": ""}},
        "agents": {"bot": {"merchants": {"blocked": ["agent-scam.com"]}}}}
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg, f); f.close()
    g = SpendShield(dry_run=False); g.load_policy(f.name); os.unlink(f.name)
    assert g.authorize("bot", 10, "global-scam.com").decision == "DENY"
    assert g.authorize("bot", 10, "agent-scam.com").decision == "DENY"


# ── 规则优先级: 先命中的致命规则决定结果 ────────────────
def test_merchant_gate_beats_amount_gate():
    """黑名单商户 + 超限金额 → merchant 先拦(decision=blocked_blacklist)"""
    cfg = {"version": "1", "policy": {
        "transaction": {"max": 10},
        "merchants": {"allowed": [], "blocked": ["scam.com"]},
        "approval": {"over": 0, "new_merchant": False, "channel": ""}}}
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg, f); f.close()
    g = SpendShield(dry_run=False); g.load_policy(f.name); os.unlink(f.name)
    r = g.authorize("", 9999, "scam.com")
    assert r.decision == "DENY"
    assert r.rules[0].rule == "merchant_blocked"


def test_budget_gate_beats_approval_gate():
    """超预算 + 需审批 → budget 先拦(不产生 APPROVAL)"""
    cfg = {"version": "1", "policy": {
        "budget": {"daily": 50}, "transaction": {"max": 1000},
        "merchants": {"allowed": [], "blocked": []},
        "approval": {"over": 10, "new_merchant": False, "channel": "tg"}}}
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg, f); f.close()
    g = SpendShield(dry_run=False); g.load_policy(f.name); os.unlink(f.name)
    r = g.authorize("", 60, "x.com")   # 超 50 预算, 也超 10 审批阈值
    assert r.decision == "DENY"
    assert r.rules[0].rule == "daily_budget"


# ── 默认策略 ─────────────────────────────────────────────
def test_empty_policy_has_safe_defaults():
    """空 policy: 无显式限制, 但保留安全默认(新商户需审批, 无通道 → 拒绝)"""
    cfg = {"version": "1", "policy": {}}
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg, f); f.close()
    g = SpendShield(dry_run=False); g.load_policy(f.name); os.unlink(f.name)
    # 新商户 + 无通道 → 安全默认拒绝(即使没有显式限制)
    assert g.authorize("", 10, "brand-new.com").decision == "DENY"
    # 显式关闭审批后: 无限制放行
    g._setup_v2({"version": "1", "policy": {"approval": {"over": 0, "new_merchant": False, "channel": ""}}})
    assert g.authorize("", 999999, "anything.com").decision == "ALLOW"


def test_no_policy_vs_empty_policy_distinct():
    """未加载 policy(引擎缺失)≠ 空 policy(引擎在): 前者必须 fail-closed"""
    g = SpendShield(dry_run=False)
    g._v2_policy = None
    g._v2_estate = None
    with pytest.raises(RuntimeError):
        g.authorize("", 10, "x.com")


def test_zero_limits_mean_unlimited():
    """0 = 不限(budget 0 / max 0 / over 0 全部不触发)"""
    cfg = {"version": "1", "policy": {
        "budget": {"daily": 0, "monthly": 0, "total": 0},
        "transaction": {"max": 0, "min": 0},
        "merchants": {"allowed": [], "blocked": []},
        "approval": {"over": 0, "new_merchant": False, "channel": ""}}}
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg, f); f.close()
    g = SpendShield(dry_run=False); g.load_policy(f.name); os.unlink(f.name)
    assert g.authorize("", 100, "x.com").decision == "ALLOW"
    assert g.authorize("", 1e9, "y.com").decision == "ALLOW"


# ── 边界值语义 ───────────────────────────────────────────
def test_boundary_equal_values():
    """恰好等于阈值: max 边界(over 关闭) / over 边界(max 放开)"""
    # max=50, over=0: 50 通过, 50.001 拒绝
    cfg = {"version": "1", "policy": {
        "transaction": {"max": 50},
        "merchants": {"allowed": [], "blocked": []},
        "approval": {"over": 0, "new_merchant": False, "channel": ""}}}
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg, f); f.close()
    g = SpendShield(dry_run=False); g.load_policy(f.name); os.unlink(f.name)
    assert g.authorize("", 50, "x.com").decision == "ALLOW"     # == max 通过
    assert g.authorize("", 50.001, "x.com").decision == "DENY"  # > max 拒绝

    # over=30, max 放开: 30 不触发审批, 30.01 触发
    cfg2 = {"version": "1", "policy": {
        "transaction": {"max": 1000},
        "merchants": {"allowed": [], "blocked": []},
        "approval": {"over": 30, "new_merchant": False, "channel": "tg"}}}
    f2 = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg2, f2); f2.close()
    g2 = SpendShield(dry_run=False); g2.load_policy(f2.name); os.unlink(f2.name)
    assert g2.authorize("", 30, "x.com").decision == "ALLOW"       # == over 不触发
    assert g2.authorize("", 30.01, "x.com").decision == "APPROVAL"  # > over 审批


def test_min_boundary():
    cfg = {"version": "1", "policy": {
        "transaction": {"max": 100, "min": 1},
        "merchants": {"allowed": [], "blocked": []},
        "approval": {"over": 0, "new_merchant": False, "channel": ""}}}
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg, f); f.close()
    g = SpendShield(dry_run=False); g.load_policy(f.name); os.unlink(f.name)
    assert g.authorize("", 1, "x.com").decision == "ALLOW"      # == min 通过
    assert g.authorize("", 0.99, "x.com").decision == "DENY"    # < min 拒绝
    assert g.authorize("", 0, "x.com").decision == "DENY"       # 0 拒
