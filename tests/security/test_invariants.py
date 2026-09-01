# -*- coding: utf-8 -*-
"""
SpendShield 安全宪法 — 系统级不变量测试

不测「这个函数返回什么」, 测「整个系统永远不能违反什么」:

  1. 未授权 → 不能付款          (unauthorized 不产生任何记账)
  2. 超预算 → 不能付款          (任何时候 spent_total <= budget)
  3. Approval 不匹配 → 不能付款 (批准绑定原始请求, 篡改无效)
  4. Identity 无效 → 不能付款   (未知 agent 安全默认拒绝)
  5. 重放 → 不能产生第二次付款  (同交易最多一次有效授权)
  6. 并发 → 不能突破预算        (100 并发也不超)
  7. 引擎故障 → 默认拒绝        (fail-closed: 无 policy / 异常状态 = 不能付款)
  8. Agent 不能绕过 SpendShield (凭证获取必须过闸门)

这些规则是 V2 的宪法, V3+ 加任何层(Intent/Risk)都不能破坏。
"""
import sys, os, threading, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from spendshield import SpendShield, GuardedError

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 100, "monthly": 1000},
        "transaction": {"max": 50},
        "merchants": {"allowed": ["amazon.com"], "allow_subdomains": True},
        "approval": {"over": 30, "new_merchant": True, "channel": "tg"},
        "rate_limit": {"window_s": 3600, "max_calls": 1000},
    },
    "agents": {"bot": {"approval": {"over": 30, "new_merchant": True, "channel": "tg"}}},
}


def _make_guard(budget=100):
    import tempfile, yaml, os
    p = dict(POLICY)
    p["policy"] = dict(POLICY["policy"], budget={"daily": budget, "monthly": 1000})
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(p, f)
        path = f.name
    g = SpendShield(dry_run=False)
    g.load_policy(path)
    os.unlink(path)
    return g


# ── 不变量 1: 未授权不能付款 ─────────────────────────────
def test_invariant_unauthorized_never_books():
    g = _make_guard()
    # 各种被拒路径: DENY / APPROVAL(未批准) 都不能记账
    g.authorize("hacker", 10, "amazon.com")                    # 未知 agent → DENY
    g.authorize("bot", 999, "amazon.com")                      # 超 max → DENY
    g.authorize("bot", 40, "amazon.com")                       # 超 over → APPROVAL(未批准)
    g.authorize("bot", 10, "evil.com")                         # 非白名单 → DENY
    assert g._v2_estate.spent_total == 0, "未授权的请求绝不能记账"
    assert len(g.pending_approvals()) == 1                     # APPROVAL 挂起但未花钱


# ── 不变量 2: 超预算不能付款(Money Invariant) ─────────────
def test_invariant_money_never_exceeds_budget():
    g = _make_guard(budget=100)
    # 随机乱序请求序列(含恶意), 任何时刻 spent <= budget
    seq = [("bot", 50, "amazon.com"), ("bot", 60, "amazon.com"), ("bot", 30, "amazon.com"),
           ("bot", 20, "amazon.com"), ("bot", 50, "amazon.com"), ("bot", 1, "amazon.com"),
           ("bot", 49, "amazon.com"), ("bot", 2, "amazon.com"), ("bot", 0, "amazon.com"),
           ("bot", -5, "amazon.com"), ("hacker", 100, "amazon.com")]
    for agent, amt, to in seq:
        try:
            g.authorize(agent, amt, to)
        except Exception:
            pass
        assert g._v2_estate.spent_total <= 100, f"Money invariant 被打破: {g._v2_estate.spent_total}"
    # 且记账 = 各 ALLOW 之和(无凭空多记)
    assert g._v2_estate.spent_total == g._spent


def test_invariant_after_approvals_money_still_bounded():
    g = _make_guard(budget=100)
    # 批准路径也必须守预算: 一直批准, 总额仍 <= 100
    for _ in range(10):
        r = g.authorize("bot", 40, "amazon.com")
        if r.decision == "APPROVAL":
            g.approve(r.approval_id, by="felix")
        assert g._v2_estate.spent_total <= 100, f"批准后超预算: {g._v2_estate.spent_total}"


# ── 不变量 3: Approval 不匹配不能付款 ─────────────────────
def test_invariant_approval_binds_original_request():
    g = _make_guard()
    r = g.authorize("bot", 40, "amazon.com")
    aid = r.approval_id
    # 攻击: 批准前篡改挂起请求的金额和收款方
    g._v2_estate.pending[aid][0].amount = 9999
    g._v2_estate.pending[aid][0].to = "evil.com"
    res = g.approve(aid, by="felix")
    # 无论结果如何, 记账金额绝不允许等于篡改值 9999
    assert g._v2_estate.spent_total != 9999
    assert g._v2_estate.spent_total <= 100


def test_invariant_approval_amount_matches_request():
    """Approval(amount=50) ≠ request(amount=500) → 必须重新授权, 不能按 50 的批准花 500"""
    g = _make_guard()
    r = g.authorize("bot", 40, "amazon.com")
    aid = r.approval_id
    g.approve(aid, by="felix")
    # 批准后想花 500(远超批准额度) → 必须重新走闸门 → DENY(max=50)
    r2 = g.authorize("bot", 500, "amazon.com")
    assert r2.decision == "DENY"
    assert g._v2_estate.spent_total == 40


# ── 不变量 4: Identity 无效不能付款 ───────────────────────
def test_invariant_invalid_identity_denied():
    g = _make_guard()
    # 无效身份(未注册) → 拒绝
    for agent in ("stranger", "root", "admin", "' OR 1=1", " "):
        r = g.authorize(agent, 10, "amazon.com")
        assert r.decision == "DENY", f"无效身份 {agent!r} 不应放行"
        assert g._v2_estate.spent_total == 0
    # 空身份(None/"")也是无效身份(宪法): 默认拒绝; allow_unknown=True 才走全局
    assert g.authorize(None, 10, "amazon.com").decision == "DENY"
    assert g.authorize("", 10, "amazon.com").decision == "DENY"


# ── 不变量 5: 重放不能产生第二次付款 ──────────────────────
def test_invariant_replay_at_most_one_authorization():
    g = _make_guard()
    decisions = []
    for i in range(10):
        r = g.authorize("bot", 10, "amazon.com", meta={"idempotency_key": "tx-1"})
        decisions.append(r.decision)
    assert decisions.count("ALLOW") == 1, f"同交易被授权了 {decisions.count('ALLOW')} 次: {decisions}"
    assert g._v2_estate.spent_total == 10


# ── 不变量 6: 并发不能突破预算 ────────────────────────────
def test_invariant_concurrent_never_exceeds_budget():
    g = _make_guard(budget=100)
    results = []
    def attack():
        results.append(g.authorize("bot", 20, "amazon.com").decision)
    threads = [threading.Thread(target=attack) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert g._v2_estate.spent_total <= 100, f"并发突破预算: {g._v2_estate.spent_total}"
    assert results.count("ALLOW") == 5


# ── 不变量 7: 引擎故障默认拒绝(fail-closed) ──────────────
def test_invariant_no_policy_no_payment():
    g = SpendShield(dry_run=False)   # 未加载任何 policy
    with pytest.raises(RuntimeError):
        g.authorize("bot", 10, "amazon.com")   # 无引擎 = 不能付款


def test_invariant_bad_policy_rejected_at_load():
    import tempfile, yaml, os
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump({"version": "1", "policy": {"transaction": {"max": -1}}}, f)
        path = f.name
    g = SpendShield(dry_run=False)
    with pytest.raises(Exception):
        g.load_policy(path)          # 坏 policy 拒绝加载, 不进入半可用状态
    os.unlink(path)
    # 拒绝加载后, authorize 仍不可用(fail-closed)
    with pytest.raises(RuntimeError):
        g.authorize("bot", 10, "amazon.com")


# ── 不变量 8: Agent 不能获得绕过 SpendShield 的能力 ───────
def test_invariant_credential_access_gated():
    """get_secret 必须过闸门: 无 vault 或未授权上下文 → 不能取到密钥"""
    g = _make_guard()
    with pytest.raises(Exception):
        g.get_secret("any_key")   # 无 vault 配置 → 拒绝


def test_invariant_policy_is_immutable_at_runtime():
    """运行时篡改 policy 对象 → 拒绝(策略指纹校验, fail-closed)"""
    g = _make_guard()
    assert g.authorize("bot", 10, "amazon.com").decision == "ALLOW"
    # 攻击: 直接改内存 policy 把 max 调大
    g._v2_policy.transaction.max = 100000
    g._v2_policy.budget.daily = 100000
    r = g.authorize("bot", 500, "amazon.com")
    assert r.decision == "DENY"
    assert "tampered" in r.reason.lower()
    # 篡改后不记账
    assert g._v2_estate.spent_total == 10
    # 合法方式改配置: 重新 load 后生效
    g.load_policy.__self__._setup_v2(dict(POLICY))
    g._v2_agents = {}
    assert g.authorize("bot", 40, "amazon.com").decision == "APPROVAL"  # 重新加载后恢复正常


# ── 审计一致性 ────────────────────────────────────────────
def test_invariant_audit_matches_state():
    """审计记录数与实际决策数一致, 审计不撒谎"""
    g = _make_guard()
    g.authorize("bot", 10, "amazon.com")
    g.authorize("bot", 40, "amazon.com")
    g.authorize("bot", 999, "amazon.com")
    v2_records = [r for r in g.records if r.decision.startswith("v2_")]
    assert len(v2_records) == 3
    assert sum(1 for r in v2_records if r.decision == "v2_allow") == 1
