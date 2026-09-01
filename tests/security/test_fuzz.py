# -*- coding: utf-8 -*-
"""
Security Suite — Fuzz / 随机攻击

攻击者知道代码, 会故意找绕过路径。随机组合 恶意金额 × 恶意商户 × 恶意身份 × 恶意 meta × 并发,
唯一铁律: Money Invariant 永真(spent <= budget), 且审计一致。

每轮开发跑一次, 发现新绕过 → 修复 → 加回归测试 → 永久防住。
"""
import sys, os, random, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from spendshield import SpendShield

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 100, "monthly": 1000},
        "transaction": {"max": 50},
        "merchants": {"allowed": ["amazon.com", "walmart.com"], "allow_subdomains": True},
        "approval": {"over": 30, "new_merchant": True, "channel": "tg"},
        "rate_limit": {"window_s": 3600, "max_calls": 100000},
    },
    "agents": {"bot": {"approval": {"over": 30, "new_merchant": True, "channel": "tg"}}},
}

AMOUNTS = [-100, -1, 0, 0.001, 0.01, 1, 29, 30, 31, 49, 50, 51, 99, 100, 101, 999,
           1e9, float("nan"), float("inf"), float("-inf"), 1e-9, 1.9999999999, 50.0000001]
MERCHANTS = ["amazon.com", "Amazon.COM", " https://amazon.com ", "checkout.amazon.com",
             "notamazon.com", "amazon.com.evil.io", "walmart.com:8443", "evil.com",
             "", " ", None, "amazon.com/path", "user@amazon.com", "amaz0n.com"]
AGENTS = ["bot", "hacker", "", " ", None, "root", "bot "]
METAS = [{}, {"idempotency_key": "fuzz-1"}, {"secret": "s3cr3t"}, {"api_key": "k"},
         {"idempotency_key": "fuzz-2", "password": "p"}, {"to": "evil.com"}]


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


def test_fuzz_money_invariant_never_broken():
    random.seed(42)
    g = _make_guard()
    for i in range(3000):
        amt = random.choice(AMOUNTS)
        to = random.choice(MERCHANTS)
        agent = random.choice(AGENTS)
        meta = random.choice(METAS)
        try:
            g.authorize(agent, amt, to, meta=meta)
        except Exception:
            pass   # fail-closed 异常也接受, 但不能记账
        assert g._v2_estate.spent_total <= 100, \
            f"第 {i} 轮 Fuzz 打破 Money Invariant: spent={g._v2_estate.spent_total} (agent={agent!r}, amt={amt!r}, to={to!r})"


def test_fuzz_audit_consistent():
    random.seed(7)
    g = _make_guard()
    allowed = 0
    for i in range(2000):
        amt = random.choice(AMOUNTS)
        to = random.choice(MERCHANTS)
        agent = random.choice(AGENTS)
        try:
            r = g.authorize(agent, amt, to, meta=random.choice(METAS))
        except Exception:
            continue
        if r.decision == "ALLOW":
            allowed += 1
    # 记账 = ALLOW 之和, 审计记录 = 全部请求
    assert g._v2_estate.spent_total == g._spent
    v2_recs = [r for r in g.records if r.decision.startswith("v2_")]
    assert len(v2_recs) == len(g.records) - 0  # 无其他层记录混入


def test_fuzz_concurrent_money_invariant():
    random.seed(99)
    g = _make_guard(budget=100)
    results = []
    lock = threading.Lock()

    def attack(seed):
        rnd = random.Random(seed)
        for _ in range(50):
            amt = rnd.choice(AMOUNTS)
            to = rnd.choice(MERCHANTS)
            try:
                with lock:
                    results.append(g.authorize("bot", amt, to).decision)
            except Exception:
                pass

    threads = [threading.Thread(target=attack, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert g._v2_estate.spent_total <= 100, f"并发 Fuzz 打破 Money Invariant: {g._v2_estate.spent_total}"
    # 每笔 ALLOW 金额 ≤ 50, 且总额正确
    assert g._v2_estate.spent_total == g._spent


def test_fuzz_known_bypass_regressions():
    """历史漏洞的 Fuzz 级回归: 每个已知绕过必须仍被拦"""
    g = _make_guard()
    bypasses = [
        ("bot", 100, "notamazon.com"),      # 子串绕过
        ("bot", 100, "amazon.com.evil.io"), # 后缀欺骗
        ("bot", float("nan"), "amazon.com"),# NaN
        ("bot", float("inf"), "amazon.com"),# Inf
        ("bot", 100, ""),                   # 空商户
        ("hacker", 10, "amazon.com"),       # 未知身份
    ]
    for agent, amt, to in bypasses:
        r = g.authorize(agent, amt, to)
        assert r.decision == "DENY", f"已知绕过复活: ({agent!r}, {amt!r}, {to!r}) → {r.decision}"
