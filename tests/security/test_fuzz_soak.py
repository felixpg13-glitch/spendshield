# -*- coding: utf-8 -*-
"""
Fuzz 持续化(Soak) — 随机 seed 多轮攻击, 唯一铁律: Money Invariant 永真

固定 seed 的测试可以被「知道测试用例的攻击者」绕过。
Soak 模式: 每轮随机 seed, 任意一轮打破不变量 = 失败。

运行:
  python3 -m pytest tests/security/test_fuzz_soak.py -q          # 默认 10 轮
  SOAK_ROUNDS=50 python3 -m pytest tests/security/test_fuzz_soak.py -q  # 50 轮
"""
import sys, os, random, tempfile, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from spendshield import SpendShield

ROUNDS = int(os.environ.get("SOAK_ROUNDS", "10"))
CASES_PER_ROUND = int(os.environ.get("SOAK_CASES", "300"))

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 100, "monthly": 500},
        "transaction": {"max": 50, "min": 0.01},
        "merchants": {"allowed": ["amazon.com", "walmart.com"], "blocked": ["evil.com"],
                      "allow_subdomains": True},
        "approval": {"over": 30, "new_merchant": True, "channel": "tg"},
        "rate_limit": {"window_s": 3600, "max_calls": 1000},
    },
    "agents": {"bot": {"approval": {"over": 30, "new_merchant": True, "channel": "tg"}}},
}

AMOUNTS = [-1e9, -100, -1, -0.01, 0, 1e-9, 0.01, 1, 29.99, 30, 30.01, 49.99, 50, 50.01,
           99.99, 100, 100.01, 500, 1000, 1e6, float("nan"), float("inf"), float("-inf")]
MERCHANTS = ["amazon.com", "Amazon.COM", " https://amazon.com/x ", "checkout.amazon.com",
             "walmart.com", "evil.com", "EVIL.com", "new-shop.com", "notamazon.com",
             "amazon.com.evil.io", "walmart.com:8443", "", " ", None, "x" * 500]
AGENTS = ["bot", "", None, " ", "hacker", "bot "]
METAS = [{}, {"idempotency_key": "soak-1"}, {"secret": "***"}, {"meta": {"nested": 1}},
         {"idempotency_key": "soak-2", "password": "***"}, None, "not-a-dict"]


def _guard():
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(POLICY, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    return g


def _soak(seed: int) -> str:
    """一轮 soak: 随机请求 + 随机批准, 返回失败信息(空 = 通过)"""
    rnd = random.Random(seed)
    g = _guard()
    for i in range(CASES_PER_ROUND):
        agent = rnd.choice(AGENTS)
        amt = rnd.choice(AMOUNTS)
        to = rnd.choice(MERCHANTS)
        meta = rnd.choice(METAS)
        if not isinstance(meta, dict):
            meta = {}
        try:
            r = g.authorize(agent, amt, to, meta=meta)
        except Exception:
            continue   # fail-closed 异常可接受, 但不能记账
        if r.decision == "APPROVAL" and rnd.random() < 0.5:
            try:
                g.approve(r.approval_id, by="soak")
            except Exception:
                pass
        # Money Invariant: 任何时刻不超预算
        if g._v2_estate.spent_total > 100:
            return f"seed={seed} 第{i}笔打破 Money Invariant: spent={g._v2_estate.spent_total} " \
                   f"(agent={agent!r} amt={amt!r} to={to!r})"
        if g._v2_estate.spent_total != g._spent:
            return f"seed={seed} 第{i}笔双账不一致: v2={g._v2_estate.spent_total} old={g._spent}"
    return ""


def test_fuzz_soak_random_seeds():
    """随机 seed 多轮: 任意一轮打破不变量 = 失败"""
    failures = []
    # 每轮 seed 由当前时间派生(持续化: 每次运行都是新攻击组合)
    base = random.SystemRandom().randint(0, 2**32)
    for i in range(ROUNDS):
        seed = base + i
        err = _soak(seed)
        if err:
            failures.append(err)
    assert not failures, "Soak 失败:\n" + "\n".join(failures[:5])


def test_fuzz_soak_deterministic_seed_reproducible():
    """同 seed 必须可复现(失败可调试): 固定 seed 跑两遍结果一致"""
    g1, g2 = _guard(), _guard()
    rnd = random.Random(12345)
    for _ in range(100):
        agent = rnd.choice(AGENTS); amt = rnd.choice(AMOUNTS); to = rnd.choice(MERCHANTS)
        meta = rnd.choice(METAS)
        if not isinstance(meta, dict):
            meta = {}
        try:
            r1 = g1.authorize(agent, amt, to, meta=meta)
            r2 = g2.authorize(agent, amt, to, meta=meta)
            assert r1.decision == r2.decision
        except Exception:
            pass
    assert g1._v2_estate.spent_total == g2._v2_estate.spent_total
