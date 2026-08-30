# -*- coding: utf-8 -*-
"""
Demo: AI Agent 自动下单, SpendGuard 拦下"99 元事故"

场景还原(2026-08-09 真实事故):
    自动化系统测试下单, dry 参数没生效, 4 单 99 元真实出码扣款。

现在, 同样的场景, 有 SpendGuard:
    1. dry_run 默认开 -> 先预览, 不会真花
    2. 关掉 dry_run 后还有 budget 兜底
    3. 大额/陌生收款方 -> 人工确认
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spendguard import SpendGuard, DryRunBlocked, BudgetExceeded, NeedsApproval


def main():
    print("=" * 50)
    print("场景: AI Agent 正在自动下单 (预算 ¥200)")
    print("=" * 50)

    guard = SpendGuard(budget=200, dry_run=True)  # 默认干跑

    @guard.protect("下单")
    def place_order(amount, to, order_id=""):
        # 真实下单逻辑(这里模拟)
        return {"ok": True, "order_id": order_id or "MOCK"}

    # --- 第一单: 干跑模式拦截 ---
    print("\n[1] Agent 尝试下单 ¥99 x 4 ...")
    for i in range(4):
        try:
            place_order(amount=99, to="麦当劳", order_id=f"T{i}")
            print(f"    ❌ 第{i+1}单真实下单了!(事故重演)")
        except DryRunBlocked as e:
            print(f"    ✅ 第{i+1}单被干跑拦截: {e}")

    # --- 第二幕: 运维关掉 dry_run, 但还有预算闸门 ---
    print("\n[2] 运维说'我确认过接口了, 关掉干跑放行'...")
    guard.dry_run = False

    try:
        for i in range(4):
            place_order(amount=99, to="麦当劳", order_id=f"T{i}")
            print(f"    ✅ 第{i+1}单 ¥99 已执行 (累计 ¥{guard.spent})")
    except BudgetExceeded as e:
        print(f"    🛑 预算闸门生效: {e}")

    print(f"\n[结算] 实际花费 ¥{guard.spent} / 预算 ¥{guard.budget}")
    print(f"        拦截记录: {guard.summary()}")

    # --- 第三幕: 人工确认大额 ---
    print("\n[3] Agent 想转 ¥9,999 给'未知供应商'...")
    guard2 = SpendGuard(budget=10000, dry_run=False, approval="console")

    @guard2.protect("转账", max_amount=5000)
    def transfer(amount, to):
        return "TRANSFERRED"

    try:
        transfer(amount=9999, to="未知供应商")
    except BudgetExceeded as e:
        print(f"    🛑 单次上限拦截: {e}")

    # --- 审计 ---
    fn = guard.export_audit("/tmp/spendguard_audit.json")
    print(f"\n[审计] 全部留痕已导出 -> {fn}")

    print("\n" + "=" * 50)
    print("结论: AI 替你花钱之前, 先过 SpendGuard 这关。")
    print("=" * 50)


if __name__ == "__main__":
    main()
