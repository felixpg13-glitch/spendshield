# -*- coding: utf-8 -*-
"""
发布前安全自查 #2(2026-09-01) — 记账同步 / 名单约束 / 信任边界 / 参数提取

修复:
  1. [严重] MCP spend_protect 记账不同步 V2 state → 预算失效(花 30 后还能花 80)
  2. [中]   get_secret 走旧闸门, 不受 V2 新格式黑名单约束
  3. [中]   trusted_prefixes 子串信任: "notamazon.com" 含 "amazon.com" 免审批 → 域边界匹配
  4. [中]   _extract_amount 模糊匹配: amount_limit 被误当金额
  5. [低]   load_policy/_setup_v2 并发无锁
"""
import sys, os, tempfile, yaml, base64
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from spendshield import SpendShield

POLICY = {
    "version": "1.0",
    "policy": {"budget": {"daily": 100}, "transaction": {"max": 1000},
               "merchants": {"allowed": [], "blocked": ["mcd_sk", "scam.com"]},
               "approval": {"over": 0, "new_merchant": False, "channel": ""}},
    "agents": {},
}


def _guard(cfg=None):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg or POLICY, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    return g


# ── 1. spend_protect 记账必须同步 V2 state ───────────────
def test_mcp_protect_books_v2_state():
    from spendshield.mcp_server import SpendShieldMCP
    g = _guard()
    m = SpendShieldMCP(g)
    r = m._dispatch("spend_protect", {"action": "x", "amount": 30, "to": "shop-a"})
    assert r["ok"] is True
    assert g._v2_estate.spent_total == 30, "spend_protect 后 V2 state 必须记账"
    assert g._spent == 30
    # 预算 100, 已花 30, 再花 80 → 必须 DENY(此前漏洞: 放行)
    r2 = g.authorize("", 80, "shop-a")
    assert r2.decision == "DENY", "spend_protect 记账不同步 = 预算失效!"


# ── 2. get_secret 受 V2 黑名单约束 ───────────────────────
def test_get_secret_blocked_by_v2_blacklist():
    from spendshield.vault import KeyVault
    mk = base64.urlsafe_b64encode(b"k" * 32).decode()
    vpath = "/tmp/ss_vault_audit2.json"
    v = KeyVault(vpath, master_key=mk)
    v.store("mcd_sk", "super-secret-value")
    g = _guard()
    g.vault = v
    try:
        with pytest.raises(Exception):
            g.get_secret("mcd_sk", agent="")
        # 黑名单应直接拦(而非仅靠审批通道缺失)
        blocked_recs = [r for r in g.records if r.decision == "blocked_blacklist"]
        assert blocked_recs, "黑名单应拦截 get_secret"
    finally:
        os.unlink(vpath)


def test_get_secret_allowed_when_not_blocked():
    from spendshield.vault import KeyVault
    mk = base64.urlsafe_b64encode(b"k" * 32).decode()
    vpath = "/tmp/ss_vault_audit2b.json"
    v = KeyVault(vpath, master_key=mk)
    v.store("api_key_1", "ok-value")
    g = _guard()
    g.vault = v
    try:
        s = g.get_secret("api_key_1", agent="")
        assert s == "ok-value"
    finally:
        os.unlink(vpath)


# ── 3. trusted_prefixes 域边界 ───────────────────────────
def test_trusted_domain_boundary():
    # whitelist 迁移的信任项 "amazon.com" 只信任真域/子域
    cfg = dict(POLICY)
    cfg["policy"] = dict(POLICY["policy"],
                         merchants={"allowed": [], "blocked": [], "allow_subdomains": True},
                         approval={"over": 0, "new_merchant": True, "channel": "tg"})
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    g.register_agent("bot", budget=1000)
    g._v2_estate.trusted_prefixes.add("amazon.com")   # 模拟 whitelist 迁移

    # 真子域 → 信任, 免审批
    assert g.authorize("bot", 10, "checkout.amazon.com").decision == "ALLOW"
    # 后缀欺骗 → 不信任, 新商户 → 审批
    assert g.authorize("bot", 10, "notamazon.com").decision == "APPROVAL"
    assert g.authorize("bot", 10, "amazon.com.evil.io").decision == "APPROVAL"


def test_trusted_chinese_prefix_keeps_substring():
    """中文信任项(无 '.')保留子串语义(V1 whitelist 兼容)"""
    g = _guard()
    g.register_agent("bot", budget=1000)
    g._v2_estate.trusted_prefixes.add("麦当劳")
    # approval new_merchant 关闭时无审批, 直接 ALLOW(信任项不影响); 验证不崩
    assert g.authorize("bot", 10, "麦当劳官方店").decision == "ALLOW"


# ── 4. _extract_amount 精确匹配 ──────────────────────────
def test_extract_amount_exact_name_wins():
    g = _guard()
    def pay(amount_limit, to, amount):
        return "ok"
    # amount 精确名优先(不是 amount_limit)
    assert g._extract_amount(pay, (1000000, "x", 50), {}) == 50.0
    # 位置传参顺序: amount 在 args[2]
    assert g._extract_amount(pay, (1000000, "x", 50), {}) == 50.0


def test_extract_amount_kwargs_precedence():
    g = _guard()
    def pay(amount, to):
        return "ok"
    assert g._extract_amount(pay, (), {"amount": 42, "to": "x"}) == 42.0
    assert g._extract_amount(pay, (7, "x"), {}) == 7.0


# ── 5. load_policy 并发安全(粗测) ────────────────────────
def test_policy_reload_while_authorizing():
    import threading
    g = _guard()
    errors = []
    def churn():
        try:
            for _ in range(20):
                g.authorize("", 1, "shop-x")
        except Exception as e:
            errors.append(e)
    def reload():
        try:
            for _ in range(20):
                g.load_policy.__self__._setup_v2(dict(POLICY))
        except Exception as e:
            errors.append(e)
    t1 = threading.Thread(target=churn)
    t2 = threading.Thread(target=reload)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert not errors, errors
