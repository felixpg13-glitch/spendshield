# -*- coding: utf-8 -*-
"""
V2 Hardening ② — 资金级不变量 + 状态机跳跃

Felix 定: 「任何无法明确证明授权成立的请求, 都不能进入 APPROVED」= fail-closed。
  畸形/缺失/类型混淆/状态跳跃 → 只能 DENY 或 ERROR, 绝不能 APPROVED/ALLOW。

状态机: PENDING(APPROVAL) → APPROVED(ALLOW) 或 REJECTED(DENY), 单向不可逆。
  不允许: reject 后 approve / approve 后二次 approve / 跨策略 approve。
"""
import sys, os, tempfile, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from spendshield import SpendShield

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 100}, "transaction": {"max": 50},
        "merchants": {"allowed": ["amazon.com"], "blocked": []},
        "approval": {"over": 30, "new_merchant": False, "channel": "tg"},
    },
    "agents": {"bot": {"approval": {"over": 30, "new_merchant": False, "channel": "tg"}}},
}


def _guard(cfg=None):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg or POLICY, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    return g


# ── 资金级不变量: 无法证明授权成立的请求不能 APPROVED ──
def test_malformed_requests_never_approved():
    """畸形请求: 类型混淆/缺失/非法值 → 绝不 APPROVED 或 ALLOW"""
    g = _guard()
    attacks = [
        ("bot", None, "amazon.com"),          # amount=None
        ("bot", "abc", "amazon.com"),         # 字符串金额
        ("bot", {"x": 1}, "amazon.com"),      # dict 金额
        ("bot", [], "amazon.com"),            # list 金额
        ("bot", 40, None),                    # to=None
        ("bot", 40, 123),                     # to=数字
        ("bot", float("nan"), "amazon.com"),  # NaN
        ("bot", float("inf"), "amazon.com"),  # Inf
        ("bot", -30, "amazon.com"),           # 负金额
        ("bot", 0, "amazon.com"),             # 0 金额
        ("bot", 40, ""),                      # 空收款方
        ("bot", 40, "amazon.com", {"meta": None}),   # meta=None
        ("bot", 40, "amazon.com", {"meta": "str"}),  # meta=字符串
    ]
    for attack in attacks:
        agent, amt, to = attack[0], attack[1], attack[2]
        meta = attack[3] if len(attack) > 3 else {}
        try:
            r = g.authorize(agent, amt, to, meta=meta)
            assert r.decision in ("DENY", "ERROR"), f"{attack} → {r.decision} 不应放行!"
        except Exception:
            pass   # 抛异常 = fail-closed, 可接受


def test_invalid_approval_id_never_succeeds():
    g = _guard()
    for aid in ("", "nonexistent", "123", None, "a" * 200):
        r = g.approve(aid, by="attacker")
        assert r.decision == "DENY", f"approve({aid!r}) 不应成功"
        rj = g.reject(aid, by="attacker")
        assert rj.decision == "DENY"


def test_approval_never_bypasses_amount_gate():
    """即使 approval 是最高权限操作, 也绝不产生超限记账"""
    g = _guard()
    # 构造: 金额 40(≤max 50)触发审批 → 批准。改 pending 为超限金额 → 批准必须被拒
    r = g.authorize("bot", 40, "amazon.com")
    assert r.decision == "APPROVAL"
    g._v2_estate.pending[r.approval_id].amount = 9999
    a = g.approve(r.approval_id, by="attacker")
    assert a.decision == "DENY", "篡改金额后的审批不能放行"
    assert g._v2_estate.spent_total == 0


# ── 状态机跳跃 ───────────────────────────────────────────
def test_reject_then_approve_denied():
    g = _guard()
    r = g.authorize("bot", 40, "amazon.com")
    aid = r.approval_id
    assert g.reject(aid, by="boss").decision == "DENY"
    a = g.approve(aid, by="attacker")
    assert a.decision == "DENY", "reject 后 approve 必须失败(状态不可逆)"


def test_approve_then_reject_denied():
    g = _guard()
    r = g.authorize("bot", 40, "amazon.com")
    aid = r.approval_id
    assert g.approve(aid, by="boss").decision == "ALLOW"
    rj = g.reject(aid, by="attacker")
    assert rj.decision == "DENY", "已批准的单不能再 reject(状态不可逆)"
    assert g._v2_estate.spent_total == 40   # 已记账不变


def test_double_approve_single_charge():
    g = _guard()
    r = g.authorize("bot", 40, "amazon.com")
    aid = r.approval_id
    assert g.approve(aid).decision == "ALLOW"
    assert g.approve(aid).decision == "DENY"
    assert g._v2_estate.spent_total == 40, "双重批准不能二次记账"


def test_stale_pending_after_reset():
    """reset 清空挂起审批: 旧 approval_id 失效"""
    g = _guard()
    r = g.authorize("bot", 40, "amazon.com")
    aid = r.approval_id
    g.reset()
    assert g.approve(aid).decision == "DENY"
    assert g.pending_approvals() == []


# ── 策略/身份异常 ────────────────────────────────────────
def test_no_engine_no_approval():
    g = _guard()
    g._v2_policy = None
    g._v2_estate = None
    with pytest.raises(Exception):
        g.authorize("bot", 10, "amazon.com")


def test_approve_with_tampered_policy_denied():
    g = _guard()
    r = g.authorize("bot", 40, "amazon.com")
    aid = r.approval_id
    g._v2_policy.transaction.max = 100000   # 攻击: 篡改策略
    a = g.approve(aid, by="attacker")
    assert a.decision == "DENY", "策略被篡改时审批必须拒绝"
    assert "tampered" in a.reason.lower()
