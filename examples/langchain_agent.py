"""LangChain Agent 接入 SpendGuard 示例

让 LangChain 的 Tool 过 SpendGuard 闸门再执行。
运行: python examples/langchain_agent.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spendguard import SpendGuard, DryRunBlocked, BudgetExceeded

guard = SpendGuard(budget=200, dry_run=True)  # 默认干跑


def guarded_place_order(amount: float, to: str) -> str:
    """给 LangChain Tool 的落地函数: 先过闸门"""
    try:
        guard._authorize("下单", amount, to)
        # 通过闸门: 这里调用真实下单
        guard._spent += amount
        return f"✅ 下单成功 ¥{amount} -> {to}"
    except (DryRunBlocked, BudgetExceeded) as e:
        return f"🚫 被拦截: {e}"


# 用 @tool 装饰后即可注册进 LangChain Agent
from langchain_core.tools import tool  # pip install langchain-core

@tool
def place_order(amount: float, to: str) -> str:
    """下单购买商品。amount=金额(元), to=收款方。"""
    return guarded_place_order(amount, to)


if __name__ == "__main__":
    print(place_order.invoke({"amount": 99, "to": "麦当劳"}))
    print(place_order.invoke({"amount": 99, "to": "麦当劳"}))
