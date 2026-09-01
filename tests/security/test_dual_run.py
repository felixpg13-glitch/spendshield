# -*- coding: utf-8 -*-
"""
双跑验证 — 旧引擎(V1 四道闸门) vs V2 Policy Engine

安全方向对比: 旧引擎拒绝的, V2 也必须拒绝(或更严格)。
V2 允许的集合 ⊆ 旧引擎允许的集合 —— 即切换引擎不引入新的花钱路径。

语义差异说明(允许 V2 更严):
  - V2 白名单 = 精确域匹配(旧引擎是子串包含, 更松)
  - V2 对 NaN/空商户/后缀欺骗 更严
  - 旧引擎 dry_run 默认 True; 双跑时都关掉
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from spendshield import SpendShield, GuardedError

# 同一策略的旧格式 + 新格式
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

V2_RAW = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 100, "monthly": 0, "total": 0},
        "transaction": {"max": 50, "min": 0.01},
        "merchants": {"allowed": ["amazon.com"], "blocked": ["evil.com"], "allow_subdomains": True},
        "approval": {"over": 0, "new_merchant": False, "channel": ""},
        "rate_limit": {"window_s": 3600, "max_calls": 0},
    },
    "agents": {"bot": {"approval": {"over": 0, "new_merchant": False}}},
}


def _old_guard():
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


def _old_decision(g, agent, amt, to):
    """旧引擎: 返回 ALLOW(通过)或 BLOCK(被拦); 模拟 protect wrapper 记账"""
    try:
        g._authorize("test", amt, to, agent=agent)
        # 真实 protect() 在函数执行成功后记账; _authorize 直连不记, 手动补
        g._spent += amt
        if agent:
            g._agent_spent[agent] = g._agent_spent.get(agent, 0.0) + amt
        return "ALLOW"
    except GuardedError:
        return "BLOCK"
    except Exception:
        return "BLOCK"   # 任何异常都视为拒绝(fail-closed)


def test_dual_run_v2_no_more_permissive_than_v1():
    old_g, v2_g = _old_guard(), _v2_guard()
    cases = [
        ("bot", 10, "amazon.com"),    # 白名单小额 → 都放
        ("bot", 60, "amazon.com"),    # 超 max → 都拦
        ("bot", 10, "evil.com"),      # 黑名单 → 都拦
        ("bot", 30, "unknown.com"),   # 非名单商户: V1 放(V2 更严拒, 允许)
        ("bot", 10, "EVIL.com"),      # 黑名单大小写 → 都拦
        ("hacker", 10, "amazon.com"), # 未知 agent(旧: 未注册默认拒) → 都拦
        ("", 10, "amazon.com"),       # 空身份 → 都拦(宪法: 无效身份)
    ]
    for agent, amt, to in cases:
        od = _old_decision(old_g, agent, amt, to)
        vd = v2_g.authorize(agent, amt, to).decision
        if od == "BLOCK":
            assert vd != "ALLOW", f"双跑违例: 旧引擎拦 ({agent!r},{amt},{to!r}), V2 却放行"
        # od == ALLOW 时 V2 可以更严(允许), 但至少要有个决策


def test_dual_run_budget_consistency():
    """双引擎预算语义一致: 累计到上限后都拒绝"""
    old_g, v2_g = _old_guard(), _v2_guard()
    # 旧引擎: 20 三次后第 4 次拦(20*4 > 100? 不, 20*3=60, 第4次 80, 第5次 100, 第6次 120 拦)
    seq = [("bot", 20, "amazon.com")] * 7
    old_out = [_old_decision(old_g, *c) for c in seq]
    v2_out = [v2_g.authorize(*c).decision for c in seq]
    # 前 5 次都 ALLOW(100 恰好), 第 6 次起旧引擎拦
    assert old_out[:5] == ["ALLOW"] * 5
    assert old_out[5:] == ["BLOCK"] * 2
    # V2: 前 5 ALLOW, 之后必须 DENY
    assert v2_out[:5] == ["ALLOW"] * 5
    assert all(d != "ALLOW" for d in v2_out[5:]), v2_out


def test_dual_run_whitelist_semantics():
    """白名单语义差异是 V2 更严(允许): 旧引擎 amazon.com 子串包含, V2 精确匹配"""
    old_g, v2_g = _old_guard(), _v2_guard()
    # 旧: "notamazon.com" 含 "amazon.com" 子串 → 白名单命中 → ALLOW(漏洞)
    # V2: 精确匹配 → DENY(修复)
    od = _old_decision(old_g, "bot", 10, "notamazon.com")
    vd = v2_g.authorize("bot", 10, "notamazon.com").decision
    assert od == "ALLOW"        # 旧引擎有这个洞
    assert vd == "DENY"         # V2 已堵
