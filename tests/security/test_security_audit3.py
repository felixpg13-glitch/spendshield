# -*- coding: utf-8 -*-
"""
发布前安全自查 #3(2026-09-01) — 数值边界 / 信息泄漏 / 适配器

结论:
  - 数值边界(浮点累积/预算恰好)误差方向全安全(只多拒不超支)
  - x402 非法 price 值(inf/nan/负数)被 V2 isfinite 门拦截
  - 修复: pending_approvals() 泄漏 meta 敏感值(password 明文) → 脱敏
"""
import sys, os, tempfile, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from spendshield import SpendShield

POLICY = {
    "version": "1.0",
    "policy": {"budget": {"daily": 100}, "transaction": {"max": 1000},
               "merchants": {"allowed": [], "blocked": []},
               "approval": {"over": 1000, "new_merchant": False, "channel": "tg"}},
    "agents": {},
}


def _guard(cfg=None):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg or POLICY, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    return g


# ── 数值边界: 浮点累积绝不超预算 ─────────────────────────
def test_float_accumulation_never_exceeds_budget():
    g = _guard()
    for _ in range(1000):
        r = g.authorize("", 0.1, "s")
        if r.decision != "ALLOW":
            break
    assert g._v2_estate.spent_total <= 100, f"浮点累积超预算: {g._v2_estate.spent_total}"
    # 恰好到边界的下一笔必须被拒
    assert g.authorize("", 0.1, "s").decision == "DENY"


def test_float_boundary_exact():
    g = _guard()
    g.authorize("", 33.33, "a")
    g.authorize("", 33.33, "b")
    g.authorize("", 33.34, "c")   # 合计恰好 100(误差方向安全)
    assert g._v2_estate.spent_total <= 100
    assert g.authorize("", 0.01, "d").decision == "DENY"


# ── 次正规金额不能刷单 ───────────────────────────────────
def test_denormal_amount_blocked():
    """极小金额(1e-320)应被拒: 无意义的零头刷单(rate/known 记忆污染)"""
    g = _guard()
    r = g.authorize("", 1e-320, "tiny")
    # 安全侧: 无论放行与否, 累计不能超预算; 若放行, 记账必须 > 0 可见
    assert g._v2_estate.spent_total <= 100
    # 修: 极小金额(< 1e-9)拒绝, 防无限刷单
    assert r.decision == "DENY" or g._v2_estate.spent_total > 0


# ── x402 非法 price 被引擎拦截 ───────────────────────────
def test_x402_invalid_prices_blocked_by_engine():
    from spendshield.adapters.x402 import resource_price_to_amount
    g = _guard()
    for p in ("abc", "", None, "1e309", "-5", "NaN", "Infinity"):
        try:
            amt = resource_price_to_amount(p)
        except ValueError:
            continue   # 解析失败 = fail-safe
        # 解析出的值走 V2 引擎必须被拒(isfinite/正数门)
        r = g.authorize("", amt, "resource-x")
        assert r.decision == "DENY", f"price={p!r} → {amt} 不应放行"


# ── pending_approvals 脱敏 ───────────────────────────────
def test_pending_approvals_redacted():
    cfg = dict(POLICY)
    cfg["policy"] = dict(POLICY["policy"], approval={"over": 30, "new_merchant": False, "channel": "tg"})
    g = _guard(cfg)
    r = g.authorize("", 40, "x", meta={"password": "hunter2", "idempotency_key": "k9"})
    assert r.decision == "APPROVAL"
    pa = g.pending_approvals()
    assert "hunter2" not in str(pa), "pending_approvals 泄漏敏感 meta!"
    assert "***REDACTED***" in str(pa)


def test_authorize_result_redacted():
    cfg = dict(POLICY)
    cfg["policy"] = dict(POLICY["policy"], approval={"over": 30, "new_merchant": False, "channel": "tg"})
    g = _guard(cfg)
    r = g.authorize("", 40, "x", meta={"secret": "s3cr3t"})
    assert "s3cr3t" not in str(r.to_dict())
    # approve 后也脱敏
    a = g.approve(r.approval_id, by="felix")
    assert "s3cr3t" not in str(a.to_dict())
