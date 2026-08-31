# -*- coding: utf-8 -*-
"""
OpenAI API 调用防护 demo

场景: Agent 调用 GPT-4 前, SpendShield 四道闸门拦截:
  预算 / 单次上限 / 频率限制 / 审计

用法:
    python3 examples/openai_guard.py

真实接入: 把 call_openai 里的模拟调用换成 openai SDK 即可。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spendshield import SpendShield, DryRunBlocked, BudgetExceeded

# 每千 token 价格(美元, 示例值)
PRICES = {"gpt-4o": 0.01, "gpt-4o-mini": 0.001, "gpt-4.1": 0.02}

def main():
    guard = SpendShield(dry_run=True, budget=5.0, approve_new_recipient=False)
    guard.register_agent("research_bot", budget=5.0,
                         rate_limit={"window_s": 60, "max_calls": 5})

    @guard.protect("openai:call", max_amount=0.5, agent="research_bot")
    def call_openai(model: str, tokens: int, amount: float):
        """真实场景里这里调 openai SDK; demo 用模拟
        amount = 本次调用费用(按 tokens × 价格算好传入, 走闸门)"""
        return {"model": model, "tokens": tokens, "cost_usd": round(amount, 4)}

    def cost_of(model: str, tokens: int) -> float:
        return PRICES.get(model, 0.01) * tokens / 1000

    print("=" * 50)
    print("OpenAI 调用防护 demo")
    print("=" * 50)

    # 1. dry_run 默认开: 什么都不真花
    print("\n[1] dry_run 模式(默认):")
    try:
        call_openai("gpt-4o", 1000, amount=cost_of("gpt-4o", 1000))
    except DryRunBlocked as e:
        print(f"    ✅ 拦截: {str(e)[:60]}")

    # 2. 关掉 dry_run, 正常调用
    print("\n[2] 正常调用(预算内):")
    guard.dry_run = False
    r = call_openai("gpt-4o", 1000, amount=cost_of("gpt-4o", 1000))
    print(f"    ✅ 放行: {r}")

    # 3. 单次超限: max_amount=0.5 拦截
    print("\n[3] 单次超限(> $0.5):")
    try:
        call_openai("gpt-4o", 100000, amount=cost_of("gpt-4o", 100000))   # $1.0
    except BudgetExceeded as e:
        print(f"    ✅ 拦截: {str(e)[:70]}")

    # 4. 预算超支: 累计 > $5.0(独立 guard, 不混频率限制)
    print("\n[4] 预算超支(累计 > $5):")
    g_budget = SpendShield(dry_run=False, budget=5.0, approve_new_recipient=False)
    g_budget.register_agent("research_bot", budget=5.0)

    @g_budget.protect("openai:call", agent="research_bot")
    def budget_call(model: str, tokens: int, amount: float):
        return {"cost_usd": round(amount, 4)}

    try:
        for i in range(600):
            budget_call("gpt-4o", 1000, amount=cost_of("gpt-4o", 1000))   # 每次 $0.01
    except BudgetExceeded:
        print(f"    ✅ 第 {i+1} 次拦截: 预算 $5.0 已花完")
        print(f"    拦截前正常调用 {i} 次(每次 $0.01)")

    # 5. 频率限制: 同 agent 60s 内最多 5 次
    print("\n[5] 频率限制(60s 内最多 5 次):")
    g2 = SpendShield(dry_run=False, approve_new_recipient=False)
    g2.register_agent("research_bot", budget=100,
                      rate_limit={"window_s": 60, "max_calls": 5})

    @g2.protect("openai:call", agent="research_bot")
    def quick_call(model: str, tokens: int):
        return {"ok": True}

    for i in range(6):
        try:
            quick_call("gpt-4o-mini", 100)
        except BudgetExceeded:
            print(f"    ✅ 第 {i+1} 次被频率限制拦截")

    # 6. 审计
    print("\n[6] 全量审计(前 5 条):")
    for rec in guard.records[:5]:
        print(f"    {rec.decision:12s} {rec.action} ${rec.amount:.2f} agent={rec.agent}")

    print("\n🎉 Demo 完成: Agent 调 OpenAI 前, 预算/单次/频率/审计全部生效")

if __name__ == "__main__":
    main()
