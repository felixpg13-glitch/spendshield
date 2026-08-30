"""AutoGen Agent 接入 SpendGuard 示例"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spendguard import SpendGuard, DryRunBlocked

guard = SpendGuard(budget=100, dry_run=True)


def spend_protected(action: str, amount: float, to: str) -> dict:
    """AutoGen 的 function 注册格式"""
    try:
        guard._authorize(action, amount, to)
        guard._spent += amount
        return {"ok": True, "spent": guard.spent}
    except DryRunBlocked as e:
        return {"ok": False, "reason": str(e)}


# AutoGen 用法:
# from autogen import ConversableAgent
# agent = ConversableAgent("buyer", llm_config=..., functions=[spend_protected])
# 或注册为工具: from autogen import register_function
if __name__ == "__main__":
    print(spend_protected("下单", 99, "麦当劳"))
