# -*- coding: utf-8 -*-
"""V2 Hardening ⑦ — Lifecycle 操作层硬化(重复apply/版本冲突/并发/失败原子性)"""
import sys, os, tempfile, yaml, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest

from spendshield import SpendShield
from spendshield.policy.lifecycle import PolicyManager, PolicyLifecycleError

BASE = {
    "version": "2.0.0",
    "policy": {"budget": {"daily": 100}, "transaction": {"max": 50},
               "merchants": {"allowed": [], "blocked": []},
               "approval": {"over": 30, "new_merchant": False, "channel": "tg"}},
}


def _setup():
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(BASE, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    return g, PolicyManager(g)


def _good(version="2.1.0", max_amount=80):
    return {"version": version,
            "policy": {"budget": {"daily": 200}, "transaction": {"max": max_amount},
                       "merchants": {"allowed": [], "blocked": []},
                       "approval": {"over": 50, "new_merchant": False, "channel": "tg"}}}


def _full_apply(pm, did, by="boss"):
    pm.validate(did, by="dev"); pm.simulate(did, by="dev"); pm.scan(did, by="dev")
    pm.review(did, by=by)
    return pm.apply(did, by=by)


# ── 版本冲突: 同 version 不能覆盖历史 ────────────────────
def test_apply_version_conflict_rejected():
    """apply version=2.0.0(已存在)→ 必须拒绝, 不能覆盖历史版本"""
    g, pm = _setup()
    d = pm.create("bad", _good(version="2.0.0", max_amount=999), by="dev")   # 冒用已存在版本号
    with pytest.raises(PolicyLifecycleError):
        _full_apply(pm, d.id)
    # 生产不应被替换成 max=999 的「2.0.0」
    assert g._v2_policy.version == "2.0.0"
    assert g._v2_policy.transaction.max == 50, "版本冲突时生产被意外替换!"


def test_version_conflict_does_not_overwrite_history():
    g, pm = _setup()
    d1 = pm.create("v1", _good("2.1.0"), by="dev")
    _full_apply(pm, d1.id)
    hist = pm._versions["2.1.0"]["policy"]["transaction"]["max"]
    assert hist == 80
    # 再次 apply 同 version 不同内容 → 历史不能被覆盖
    d2 = pm.create("v1dup", _good("2.1.0", max_amount=500), by="dev")
    with pytest.raises(PolicyLifecycleError):
        _full_apply(pm, d2.id)
    assert pm._versions["2.1.0"]["policy"]["transaction"]["max"] == 80, "历史版本被覆盖!"


# ── 重复 Apply ───────────────────────────────────────────
def test_double_apply_rejected():
    g, pm = _setup()
    d = pm.create("x", _good(), by="dev")
    _full_apply(pm, d.id)
    with pytest.raises(PolicyLifecycleError):
        pm.apply(d.id, by="boss")   # 已 APPLIED, 终态不可再 apply


# ── 重复 Rollback(幂等) ─────────────────────────────────
def test_rollback_same_version_idempotent():
    g, pm = _setup()
    d = pm.create("v2.1", _good(), by="dev")
    _full_apply(pm, d.id)
    assert g._v2_policy.version == "2.1.0"
    pm.rollback("2.0.0", by="boss")
    assert g._v2_policy.version == "2.0.0"
    pm.rollback("2.0.0", by="boss")   # 重复回滚同一版本 → 幂等, 不崩
    assert g._v2_policy.version == "2.0.0"


# ── Apply 失败原子性: 坏策略不破坏现状 ──────────────────
def test_apply_failure_keeps_current_state():
    """apply 一个校验失败的策略 → 生产保持原状, 不半切换"""
    g, pm = _setup()
    before_fp = g._v2_policy_fp
    d = pm.create("bad", {"version": "9.9", "policy": {"transaction": {"max": -5}}}, by="dev")
    with pytest.raises(Exception):
        _full_apply(pm, d.id)   # validate 或 apply 阶段失败
    assert g._v2_policy.version == "2.0.0", "失败后生产被破坏!"
    assert g._v2_policy_fp == before_fp
    assert g.authorize("", 30, "x.com").decision in ("ALLOW", "APPROVAL")   # 原策略仍工作


# ── 并发 Apply / Rollback ────────────────────────────────
def test_concurrent_apply_single_winner():
    """并发 apply 同一版本: 只有 1 个赢家, 其余版本冲突被拒"""
    g, pm = _setup()
    # 同版本不同内容并发: 只有 1 个赢家, 其余撞版本冲突
    drafts = [pm.create(f"d{i}", _good("3.0.0", max_amount=80 + i * 5), by="dev") for i in range(3)]
    for d in drafts:
        pm.validate(d.id, by="dev"); pm.simulate(d.id, by="dev"); pm.scan(d.id, by="dev")
        pm.review(d.id, by="boss")
    results = []
    def do_apply(did):
        try:
            results.append(("ok", pm.apply(did, by="boss")["version"]))
        except Exception as e:
            results.append(("err", str(e)[:40]))
    threads = [threading.Thread(target=do_apply, args=(d.id,)) for d in drafts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1, f"并发 apply 同版本应只有 1 个成功: {results}"
    assert len(errs) == 2
    assert all("已存在" in r[1] for r in errs), f"失败原因应是版本冲突: {errs}"
    # 审计链完整
    ok, msg = g.audit.verify_chain()
    assert ok, msg
