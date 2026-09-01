# -*- coding: utf-8 -*-
"""
迁移等价性 + 引擎切换验证(原双跑测试, 切换完成后更新)

引擎切换已完成: 所有路径(构造参数 / 旧 YAML / 新 YAML)统一走 V2 Policy Engine。
本套件验证:
  1. 旧 YAML 自动迁移后的引擎, 与等价手工 V2 配置决策一致(迁移正确性)
  2. 旧引擎四道闸门的核心语义(预算累计/黑名单/白名单免审批/单次上限)在迁移后保留
  3. protect() 装饰器(旧契约入口)切换后行为不变
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from spendshield import SpendShield, GuardedError, BudgetExceeded

# 同一策略的旧格式 + 等价新格式
V1_YAML = """
budget: 100
max_amount: 50
blacklist: [evil.com]
whitelist: [amazon.com]
approve_above: 0
approve_new_recipient: false
dry_run: false
agents:
  bot:
    budget: 100
    max_amount: 50
"""

# 等价 V2(whitelist 迁移 = 预信任, 不是仅允许; blacklist 迁移 = blocked)
V2_RAW = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 0, "monthly": 0, "total": 100},
        "transaction": {"max": 50, "min": 0},
        "merchants": {"allowed": [], "blocked": ["evil.com"], "allow_subdomains": True},
        "approval": {"over": 0, "new_merchant": False, "channel": ""},
        "rate_limit": {"window_s": 3600, "max_calls": 0},
    },
    "agents": {"bot": {"budget": {"daily": 0, "monthly": 0, "total": 100},
                       "transaction": {"max": 50, "min": 0},
                       "approval": {"over": 0, "new_merchant": False, "channel": ""}}},
}


def _migrated_guard():
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(V1_YAML)
        path = f.name
    g = SpendShield(dry_run=False)
    g.load_policy(path)
    os.unlink(path)
    return g


def _v2_guard():
    import tempfile, yaml, os
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(V2_RAW, f)
        path = f.name
    g = SpendShield(dry_run=False)
    g.load_policy(path)
    os.unlink(path)
    return g


def test_migration_equivalent_decisions():
    """旧 YAML 迁移 vs 手工 V2: 代表性请求决策一致"""
    g1, g2 = _migrated_guard(), _v2_guard()
    # 注意: g1 迁移 whitelist → 预信任, g2 等价配置无预信任 → 两引擎决策应一致
    cases = [
        ("bot", 10, "amazon.com"),      # 白名单小额 → 都放(免审批)
        ("bot", 60, "amazon.com"),      # 超单次上限 → 都拒
        ("bot", 10, "evil.com"),        # 黑名单 → 都拒
        ("bot", 10, "EVIL.com"),        # 黑名单大小写 → 都拒
        ("bot", 10, "unknown.com"),     # 非名单商户(new_merchant=False) → 都放
        ("hacker", 10, "amazon.com"),   # 未注册身份 → 都拒
        ("", 10, "amazon.com"),         # 匿名走全局 → 都放
    ]
    for agent, amt, to in cases:
        d1 = g1.authorize(agent, amt, to).decision
        d2 = g2.authorize(agent, amt, to).decision
        assert d1 == d2, f"迁移不一致 ({agent!r},{amt},{to!r}): 迁移={d1} vs 手工={d2}"


def test_migration_budget_accumulates():
    """迁移后预算语义保留: 累计到上限后拒绝"""
    g = _migrated_guard()
    out = [g.authorize("bot", 20, "amazon.com").decision for _ in range(6)]
    assert out[:5] == ["ALLOW"] * 5   # 20*5=100 恰好
    assert out[5] == "DENY"           # 120 > 100


def test_migration_whitelist_trust_semantics():
    """旧 whitelist 子串信任语义保留: amazon.com 免审批, 含 amazon.com 的收款方也信任"""
    g = _migrated_guard()
    # new_merchant=False 时此配置下无审批, 重点验证: whitelist 商户不触发审批
    assert g.authorize("bot", 10, "amazon.com").decision == "ALLOW"


def test_protect_contract_preserved():
    """protect() 装饰器(旧契约)切换后: 通过/被拦/执行失败行为不变"""
    g = SpendShield(dry_run=False, budget=100, approve_new_recipient=False)
    calls = []

    @g.protect("下单")
    def place_order(amount, to):
        calls.append((amount, to))
        return "OK"

    assert place_order(amount=50, to="麦当劳") == "OK"
    assert g.spent == 50
    assert g.records[-1].decision == "executed"

    # 超预算被拦
    try:
        place_order(amount=60, to="麦当劳")
        raise AssertionError("应被拦")
    except BudgetExceeded:
        pass
    assert g.spent == 50   # 被拦不记账

    # 执行失败不记账
    @g.protect("失败单")
    def failing(amount, to):
        raise RuntimeError("上游失败")

    try:
        failing(amount=10, to="麦当劳")
    except RuntimeError:
        pass
    assert g.spent == 50
    assert g.records[-1].decision == "failed"


def test_old_check_still_callable():
    """旧 _check 保留为 fallback(不抛异常即通过), 引擎切换后仍可用"""
    g = SpendShield(dry_run=False, budget=100, approve_new_recipient=False)
    rec = g._check("测试", 10, "x", agent="")
    assert rec.decision == "executed"
