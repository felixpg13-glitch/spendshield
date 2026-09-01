# -*- coding: utf-8 -*-
"""
Security Suite ⑦ — parameter_tampering
攻击: 通过大小写 / 空白 / 协议前缀 / 端口 / 路径 / 后缀欺骗绕过商户白名单。
断言: 任何 tamper 都不能命中白名单(真正子域除外, 按配置)。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from spendshield import SpendShield

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 1000},
        "transaction": {"max": 100},
        "merchants": {"allowed": ["amazon.com", "walmart.com"], "allow_subdomains": True},
        "approval": {"over": 0, "new_merchant": False, "channel": ""},
    },
    "agents": {"bot": {"approval": {"over": 0, "new_merchant": False}}},
}


def _make_guard():
    import tempfile, yaml, os
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(POLICY, f)
        path = f.name
    g = SpendShield(dry_run=False)
    g.load_policy(path)
    os.unlink(path)
    return g


# ── 应 ALLOW: 真正等价于白名单商户 ──────────────────────
def test_uppercase_normalized():
    g = _make_guard()
    assert g.authorize("bot", 10, "Amazon.COM").decision == "ALLOW"


def test_whitespace_normalized():
    g = _make_guard()
    assert g.authorize("bot", 10, "  amazon.com  ").decision == "ALLOW"


def test_protocol_prefix_normalized():
    g = _make_guard()
    assert g.authorize("bot", 10, "https://amazon.com").decision == "ALLOW"
    assert g.authorize("bot", 10, "http://www.amazon.com").decision == "ALLOW"


def test_real_subdomain_allowed_when_configured():
    g = _make_guard()
    assert g.authorize("bot", 10, "checkout.amazon.com").decision == "ALLOW"


# ── 应 DENY: 名单绕过攻击 ───────────────────────────────
def test_suffix_spoof_denied():
    """amazon.com.evil.com / amazon.com.attacker.io → 不得命中 amazon.com"""
    g = _make_guard()
    for to in ("amazon.com.evil.com", "amazon.com.attacker.io", "notamazon.com",
               "amazon.com.ph", "amazoncom", "amazon.com@evil.com"):
        r = g.authorize("bot", 10, to)
        assert r.decision == "DENY", f"{to} 不应通过"


def test_path_port_tamper_denied_or_safe():
    """路径/端口/用户信息 → 规范后不命中白名单(安全侧)"""
    g = _make_guard()
    for to in ("amazon.com:8443", "amazon.com/shop", "https://amazon.com.evil.com/path",
               "user:pass@amazon.com", "amazon.com#fragment"):
        r = g.authorize("bot", 10, to)
        assert r.decision == "DENY", f"{to} 不应通过"


def test_subdomain_spoof_with_subdomains_disabled():
    """allow_subdomains=False 时, 连真子域都拒绝"""
    g = _make_guard()
    g._v2_policy.merchants.allow_subdomains = False
    assert g.authorize("bot", 10, "checkout.amazon.com").decision == "DENY"
    assert g.authorize("bot", 10, "amazon.com").decision == "ALLOW"


def test_blocklist_tamper():
    """黑名单同样规范化: 大小写/协议不能绕过黑名单"""
    g = _make_guard()
    g._v2_policy.merchants.allowed = []
    g._v2_policy.merchants.blocked = ["scam.com"]
    assert g.authorize("bot", 10, "SCAM.com").decision == "DENY"
    assert g.authorize("bot", 10, "https://scam.com").decision == "DENY"
    assert g.authorize("bot", 10, "scam.com.evil.io").decision == "ALLOW"  # 不同域
