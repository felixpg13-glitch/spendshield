# -*- coding: utf-8 -*-
"""
Security Suite ② — race_condition
攻击: 多线程同时扣预算 / 同时审批, 试图让记账不一致。
断言: 最终 state 与决策数一致(无丢失/重复记账)。
"""
import sys, os, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from spendshield import SpendShield

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 10000},
        "transaction": {"max": 1000},
        "merchants": {"allowed": ["amazon.com"]},
        "approval": {"over": 50, "new_merchant": False, "channel": "tg"},
    },
    "agents": {"racer": {"approval": {"over": 50, "new_merchant": False, "channel": "tg"}}},
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


def test_concurrent_approve_no_duplicate_booking():
    """并发批准同一 approval_id: 只能成功一次(幂等), 只记账一次"""
    g = _make_guard()
    r = g.authorize("racer", 60, "amazon.com")   # >50 → APPROVAL
    assert r.decision == "APPROVAL"
    aid = r.approval_id

    outcomes = []
    def approve():
        outcomes.append(g.approve(aid, by="felix").decision)

    threads = [threading.Thread(target=approve) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("ALLOW") == 1, f"批准应只成功一次: {outcomes}"
    assert g._v2_estate.spent_total == 60, f"应只记账一次: {g._v2_estate.spent_total}"


def test_concurrent_authorize_booking_matches():
    """30 线程各 10: 全部 ALLOW, 记账 = 300, 无丢失"""
    g = _make_guard()
    def attack():
        g.authorize("racer", 10, "amazon.com")
    threads = [threading.Thread(target=attack) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert g._v2_estate.spent_total == 300, f"记账丢失: {g._v2_estate.spent_total}"
    # rate_hits 与成功数一致
    assert len(g._v2_estate.rate_hits) == 30


def test_rate_limit_window_race():
    """并发打满频率窗口: 超过 max_calls 的必须被拒"""
    g = _make_guard()
    # 改窗口: max_calls=5
    g._v2_policy.rate_limit.max_calls = 5
    g._v2_policy.rate_limit.window_s = 3600
    results = []
    def attack():
        results.append(g.authorize("racer", 10, "amazon.com").decision)
    threads = [threading.Thread(target=attack) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ALLOW") == 5, f"窗口应只放 5: {results}"
