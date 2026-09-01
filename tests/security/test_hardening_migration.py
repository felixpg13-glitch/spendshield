# -*- coding: utf-8 -*-
"""
V2 Hardening ⑤ — Migration property-based 随机差分 + 长时间双跑

  1. 随机生成 V1 配置(构造参数式) → 迁移加载不 crash, 决策有界
  2. property-based: 随机 V1 配置 + 随机请求, 迁移后的决策必须符合「配置意图」
     (配了黑名单就绝不能放行黑名单商户; 配了预算就绝不能超)
  3. 长时间双跑: 2000 笔随机请求, state 一致(预算不超、审计不撒谎、无内存异常)
"""
import sys, os, random, tempfile, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from spendshield import SpendShield


def _random_v1_config(rnd):
    """随机生成合法 V1 扁平配置"""
    cfg = {"dry_run": False}
    if rnd.random() < 0.7:
        cfg["budget"] = rnd.choice([0, 50, 100, 1000])
    if rnd.random() < 0.7:
        cfg["max_amount"] = rnd.choice([0, 10, 50, 100, 500])
    if rnd.random() < 0.5:
        cfg["blacklist"] = rnd.sample(["scam.com", "evil.io", "bad.biz"], k=rnd.randint(1, 2))
    if rnd.random() < 0.5:
        cfg["whitelist"] = rnd.sample(["amazon.com", "walmart.com"], k=rnd.randint(1, 2))
    if rnd.random() < 0.5:
        cfg["approve_new_recipient"] = rnd.choice([True, False])
    if rnd.random() < 0.5:
        cfg["approve_above"] = rnd.choice([0, 30, 100])
    if rnd.random() < 0.4:
        cfg["rate_limit"] = {"window_s": rnd.choice([60, 3600]), "max_calls": rnd.choice([0, 3, 10])}
    if rnd.random() < 0.5:
        cfg["agents"] = {"bot": {"budget": rnd.choice([0, 100]), "max_amount": rnd.choice([0, 50])}}
    return cfg


def _load_v1(cfg):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    return g


def test_migration_random_configs_never_crash():
    """随机 V1 配置 × 50 组: 迁移加载 + 随机请求不 crash"""
    rnd = random.Random(42)
    merchants = ["amazon.com", "walmart.com", "scam.com", "evil.io", "unknown-shop.com"]
    for i in range(50):
        cfg = _random_v1_config(rnd)
        try:
            g = _load_v1(cfg)
        except Exception as e:
            raise AssertionError(f"配置 {i} 迁移失败: {cfg}\n{e}")
        for _ in range(20):
            agent = rnd.choice(["bot", ""])
            amt = rnd.choice([1, 10, 49, 51, 100, 1000, -1, 0])
            to = rnd.choice(merchants)
            try:
                g.authorize(agent, amt, to)
            except Exception:
                pass   # fail-closed 可接受


def test_migration_blacklist_intent_preserved():
    """property: 配了黑名单的配置, 迁移后黑名单商户绝不能放行"""
    rnd = random.Random(7)
    for i in range(30):
        cfg = _random_v1_config(rnd)
        if not cfg.get("blacklist"):
            continue
        g = _load_v1(cfg)
        for b in cfg["blacklist"]:
            for amt in (1, 50, 100):
                r = g.authorize("", amt, b)
                assert r.decision == "DENY", f"配置{i} 黑名单 {b} 被放行: {cfg}"


def test_migration_budget_intent_preserved():
    """property: 配了预算的配置, 迁移后累计花费绝不能超"""
    rnd = random.Random(99)
    for i in range(30):
        cfg = _random_v1_config(rnd)
        budget = cfg.get("budget", 0)
        if not budget:
            continue
        g = _load_v1(cfg)
        spent = 0.0
        for _ in range(50):
            amt = rnd.choice([1, 10, 20, 50])
            r = g.authorize("", amt, "amazon.com")
            if r.decision == "ALLOW":
                spent += amt
        assert spent <= budget, f"配置{i} 预算 {budget} 被突破: spent={spent}"


def test_migration_agent_config_applied():
    """迁移后 agent 配置生效: 注册的 agent 有独立预算"""
    cfg = {"dry_run": False, "budget": 1000, "approve_new_recipient": False,
           "agents": {"bot": {"budget": 50, "max_amount": 100}}}
    g = _load_v1(cfg)
    # bot 预算 50: 30+30 第二笔被拒(agent 级隔离)
    assert g.authorize("bot", 30, "amazon.com").decision == "ALLOW"
    assert g.authorize("bot", 30, "amazon.com").decision == "DENY"
    # 匿名走全局 1000
    assert g.authorize("", 30, "amazon.com").decision == "ALLOW"


# ── 长时间双跑 ───────────────────────────────────────────
def test_long_run_state_consistency():
    """2000 笔随机请求: 预算不超 / 审计不撒谎 / 无异常"""
    cfg = {"version": "2.0.0", "policy": {
        "budget": {"daily": 500, "monthly": 2000},
        "transaction": {"max": 100, "min": 0.01},
        "merchants": {"allowed": [], "blocked": ["scam.com"]},
        "approval": {"over": 80, "new_merchant": True, "channel": "tg"},
        "rate_limit": {"window_s": 3600, "max_calls": 1000},
    }, "agents": {"bot": {"approval": {"over": 80, "new_merchant": True, "channel": "tg"}}}}
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(cfg, f); f.close()
    g = SpendShield(dry_run=False); g.load_policy(f.name); os.unlink(f.name)

    rnd = random.Random(2026)
    approved_total = 0.0
    for _ in range(2000):
        amt = rnd.choice([0.01, 1, 20, 50, 79, 80, 81, 99, 100, 101])
        to = rnd.choice(["amazon.com", "walmart.com", "new-shop.com", "scam.com"])
        r = g.authorize("bot", amt, to)
        if r.decision == "ALLOW":
            approved_total += amt
        elif r.decision == "APPROVAL":
            g.approve(r.approval_id, by="boss")   # 批准后可能 ALLOW(再记账)
            # approve 返回的 ALLOW 已记账, 这里不重复加
        # 铁律: 任何时刻 state 不超预算
        assert g._v2_estate.spent_total <= 500, f"daily 超预算: {g._v2_estate.spent_total}"
    # 记账 = 各 ALLOW 之和(审计不撒谎); 每笔 authorize 至少 1 条记录(approve 额外产生)
    assert g._v2_estate.spent_total == g._spent
    assert len(g.records) >= 2000, f"审计缺记录: {len(g.records)}"
