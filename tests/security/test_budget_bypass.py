# -*- coding: utf-8 -*-
"""
Security Suite ① — budget_bypass
攻击: 预算 $100, 并发两笔 $60, 试图让总额变成 $120。
断言: 总额必须 ≤ $100(评估-记账在锁内原子完成, 防 TOCTOU)。
"""
import sys, os, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from spendshield import SpendShield

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 100},
        "transaction": {"max": 1000},
        "merchants": {"allowed": ["amazon.com"]},
        "approval": {"over": 0, "new_merchant": False, "channel": ""},
    },
    "agents": {"attacker": {"approval": {"over": 0, "new_merchant": False}}},
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


def test_concurrent_budget_bypass():
    g = _make_guard()
    results, errors = [], []

    def attack():
        try:
            r = g.authorize("attacker", 60, "amazon.com")
            results.append(r.decision)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=attack) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    # 并发两笔 60: 预算 100 → 最多一笔 ALLOW
    assert results.count("ALLOW") == 1, f"两笔 60 都过了? results={results}"
    assert results.count("DENY") == 1
    assert g._v2_estate.spent_total <= 100, f"总额超预算: {g._v2_estate.spent_total}"


def test_sequential_budget_bypass():
    g = _make_guard()
    r1 = g.authorize("attacker", 60, "amazon.com")
    r2 = g.authorize("attacker", 60, "amazon.com")
    assert r1.decision == "ALLOW"
    assert r2.decision == "DENY"
    assert g._v2_estate.spent_total == 60


def test_many_threads_budget_never_exceeds():
    g = _make_guard()
    results = []
    def attack():
        results.append(g.authorize("attacker", 20, "amazon.com").decision)
    threads = [threading.Thread(target=attack) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert g._v2_estate.spent_total == 100, f"10×20 并发应恰好 100: {g._v2_estate.spent_total}"
    assert results.count("ALLOW") == 5, f"应只有 5 笔过: {results}"
    assert results.count("DENY") == 5
