# -*- coding: utf-8 -*-
"""
V2 Hardening ① — Simulator 与真实 authorize 语义一致性

核心要求(Felix 定): Simulator 结果必须与真实 authorize 严格一致。
不能出现「模拟 ALLOW、真实 DENY」这类未定义差异。

方法: 同一策略、同一请求序列, 两个独立实例(simulator / guard)各自演化,
逐步断言每次决策一致。共享 state 会因记账时序产生假分叉, 故用独立实例。
"""
import sys, os, random, tempfile, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from spendshield import SpendShield
from spendshield.policy import PolicySimulator

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 100, "monthly": 500},
        "transaction": {"max": 50, "min": 0.01},
        "merchants": {"allowed": ["amazon.com", "walmart.com"], "blocked": ["evil.com"],
                      "allow_subdomains": True},
        "approval": {"over": 30, "new_merchant": True, "channel": "tg"},
        "rate_limit": {"window_s": 3600, "max_calls": 5, "max_total": 200},
    },
    "agents": {
        "bot": {"transaction": {"max": 50},
                "approval": {"over": 30, "new_merchant": True, "channel": "tg"}},
        "big": {"transaction": {"max": 500},
                "approval": {"over": 300, "new_merchant": False, "channel": "tg"}},
    },
}


def _guard(cfg=None):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg or POLICY, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    return g


def _random_cases(n, seed):
    random.seed(seed)
    out = []
    for _ in range(n):
        agent = random.choice(["bot", "big", ""])
        amt = random.choice([0.01, 1, 20, 29, 30, 31, 49, 50, 51, 80, 200, 500, 501, -5, 0, float("nan")])
        to = random.choice(["amazon.com", "checkout.amazon.com", "walmart.com", "evil.com",
                            "new-shop.com", "notamazon.com", "amazon.com.evil.io", ""])
        out.append((agent, amt, to))
    return out


def test_simulator_matches_real_authorize_sequence():
    """同一请求序列, sim 与 real 各自演化, 每步决策必须一致"""
    g = _guard()
    sim = PolicySimulator(policy_raw=POLICY)
    for i, (agent, amt, to) in enumerate(_random_cases(300, 20260901)):
        sim_r = sim.evaluate(agent, amt, to)
        real_r = g.authorize(agent, amt, to, book=True)
        assert sim_r.decision == real_r.decision, (
            f"第 {i} 笔不一致! ({agent!r},{amt!r},{to!r})\n"
            f"  sim={sim_r.decision}({sim_r.reason})\n"
            f"  real={real_r.decision}({real_r.reason})")


def test_simulator_allow_never_beats_real():
    """铁律: sim 说 ALLOW 的, 真实绝不能 DENY"""
    g = _guard()
    sim = PolicySimulator(policy_raw=POLICY)
    for i, (agent, amt, to) in enumerate(_random_cases(500, 7)):
        amt = float(f"{amt:.2f}") if isinstance(amt, float) and amt > 0 else amt
        sim_r = sim.evaluate(agent, amt, to)
        real_r = g.authorize(agent, amt, to, book=True)
        if sim_r.decision == "ALLOW":
            assert real_r.decision == "ALLOW", (
                f"模拟 ALLOW 但真实 {real_r.decision}: 第{i}笔 ({agent},{amt!r},{to}) "
                f"sim={sim_r.reason} real={real_r.reason}")


def test_simulator_approval_channel_semantics_match():
    """无审批通道: sim 与 real 都必须 DENY(不允许 sim APPROVAL / real DENY)"""
    cfg = dict(POLICY)
    cfg["policy"] = dict(POLICY["policy"], approval={"over": 30, "new_merchant": True, "channel": ""})
    cfg["agents"] = {"bot": {"approval": {"over": 30, "new_merchant": True}}}   # 无 channel → 继承全局 ""
    g = _guard(cfg)
    sim = PolicySimulator(policy_raw=cfg)
    for amt in (31, 40, 80):
        r_sim = sim.evaluate("bot", amt, "amazon.com")
        r_real = g.authorize("bot", amt, "amazon.com", book=True)
        assert r_sim.decision == r_real.decision == "DENY", (
            f"{amt}: sim={r_sim.decision} real={r_real.decision} (无通道必须都 DENY)")


def test_simulator_does_not_mutate_guard_state():
    """Simulator 评估不能污染真实 state(独立实例时)"""
    g = _guard()
    sim = PolicySimulator(policy_raw=POLICY)   # 独立 state
    sim.evaluate("bot", 40, "amazon.com")
    sim.evaluate("bot", 10, "amazon.com")
    assert g._v2_estate.spent_total == 0, "sim 评估污染了真实 state!"
