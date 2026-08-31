# -*- coding: utf-8 -*-
"""
三通道 Demo: 同一个策略, 切换 x402 / Stripe / 支付宝, 策略零改动

核心卖点展示: 策略与通道完全解耦
  - SpendShield 管「该不该付」(预算/限额/白名单/审批)
  - 支付通道是你的代码(执行器), 随时可插拔
  - 换通道 = 换函数, 策略一行不改 —— 防厂商锁定

用法:
    python3 examples/three_channel_demo.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spendshield import SpendShield, BudgetExceeded


def main():
    # ===== 1. 策略只写一次(预算 $100, 单笔 $50, 白名单 openai.com) =====
    guard = SpendShield(dry_run=False, budget=100, approve_new_recipient=False,
                        blacklist=["evil.example.com"])
    guard.register_agent("demo_agent", budget=100, whitelist=["openai.com"])

    # ===== 2. 三个支付通道执行器(模拟, 真实场景换成官方 SDK) =====
    def x402_execute(amount, to):
        # 真实场景: x402 协议链上结算
        return {"tx": "0xabc123", "channel": "x402", "amount": amount, "to": to}

    def stripe_execute(amount, to):
        # 真实场景: stripe.PaymentIntent.create(...)
        return {"tx": "stripe_pi_456", "channel": "stripe", "amount": amount, "to": to}

    def alipay_execute(amount, to):
        # 真实场景: 支付宝当面付/APP 支付
        return {"tx": "alipay_789", "channel": "alipay", "amount": amount, "to": to}

    # ===== 3. 同一策略罩住三个通道(通道 = 函数, 策略 = guard) =====
    @guard.protect("下单", max_amount=50, agent="demo_agent")
    def pay_x402(amount, to):
        return x402_execute(amount, to)

    @guard.protect("下单", max_amount=50, agent="demo_agent")
    def pay_stripe(amount, to):
        return stripe_execute(amount, to)

    @guard.protect("下单", max_amount=50, agent="demo_agent")
    def pay_alipay(amount, to):
        return alipay_execute(amount, to)

    # ===== 4. 跑三个通道, 策略完全不变 =====
    print("=" * 56)
    print("同一个策略(预算$100 / 单笔$50 / 白名单openai.com)")
    print("切换三个支付通道, 策略零改动")
    print("=" * 56)

    for name, fn in [("x402   ", pay_x402), ("stripe ", pay_stripe), ("alipay ", pay_alipay)]:
        # 30 美元: 预算内 + 限额内 + 白名单 → 放行
        try:
            r = fn(amount=30, to="openai.com")
            print(f"[{name}] ✅ $30 → openai.com 放行: {r['channel']} tx={r['tx'][:12]}")
        except Exception as e:
            print(f"[{name}] ❌ $30 意外拦截: {e}")

        # 80 美元: 超单笔上限 $50 → 拒(三个通道都拦, 策略一致)
        try:
            fn(amount=80, to="openai.com")
            print(f"[{name}] ❌ $80 竟然放行了?! 策略失效")
        except BudgetExceeded:
            print(f"[{name}] ❌ $80 被拒: 单笔上限 $50")

        # 黑名单收款方 → 拒
        try:
            fn(amount=10, to="evil.example.com")
            print(f"[{name}] ❌ 黑名单收款方竟然放行了?!")
        except BudgetExceeded:
            print(f"[{name}] ❌ $10 → evil.example.com 被拒: 黑名单")

    print("\n🎉 三通道 Demo 完成: 策略不变, 通道可换 —— 防厂商锁定")

if __name__ == "__main__":
    main()
