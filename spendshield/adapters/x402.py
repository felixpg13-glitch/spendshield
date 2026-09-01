# -*- coding: utf-8 -*-
"""
x402 适配层 — 让 x402 支付过 SpendShield 闸门

x402 = 面向互联网的支付协议(HTTP 402),让 AI Agent 能付费调用 API。
SpendShield = 付款前护栏。二者互补:
  - x402 让 agent 能付钱
  - SpendShield 让 agent 不乱付钱(身份/预算/审批/审计)

两种集成形态:

1. 服务端(付费 API 提供方):每个资源(ResourceConfig)的支付请求
   在结算前过 SpendShield —— 预算/新收款方审批/审计自动生效。

2. 客户端(agent 调用方):发起 x402 支付前先过闸门,
   防提示注入的 agent 乱付钱。

用法:
    from spendshield import SpendShield
    from spendshield.adapters.x402 import X402PaywallGuard, protect_x402_payment

    guard = SpendShield(budget=50, dry_run=True)
    pw = X402PaywallGuard(guard, action_prefix="x402")

    # 服务端: 包装 x402 资源(在支付处理流程中调用)
    ok = pw.authorize_resource("weather-api", price="0.01", asset="USDC", pay_to="0x...")

    # 客户端: 支付前过闸门
    protect_x402_payment(guard, amount=0.01, to="weather.example.com", agent="research_bot")
"""
from __future__ import annotations

from typing import Any, Optional

from ..guard import SpendShield, GuardedError


def resource_price_to_amount(price: Any) -> float:
    """把 x402 ResourceConfig.price 转成 float 金额。
    支持: str/int/float / AssetAmount(amount, asset) / dict。"""
    if price is None:
        return 0.0
    if isinstance(price, (int, float)):
        return float(price)
    if isinstance(price, str):
        return float(price)
    # AssetAmount 或 dict
    if hasattr(price, "amount"):
        return float(price.amount)
    if isinstance(price, dict):
        return float(price.get("amount", 0))
    raise TypeError(f"无法解析 x402 金额: {price!r}")


class X402PaywallGuard:
    """服务端: 给 x402 付费资源加 SpendShield 护栏。

    在 x402 支付流程的结算前调用 authorize_resource():
    - 通过 → 继续结算
    - 被拦(预算/黑名单/审批)→ 抛 GuardedError, 支付不应继续
    """

    def __init__(self, guard: SpendShield, action_prefix: str = "x402", agent: str = ""):
        self.guard = guard
        self.action_prefix = action_prefix
        self.agent = agent

    def authorize_resource(self, resource: str, price: Any,
                           pay_to: str = "", asset: str = "",
                           max_amount: float = 0.0) -> bool:
        """结算前闸门。通过返回 True; 被拦抛 GuardedError。
        resource: 资源名(如 weather-api)
        price: x402 价格(ResourceConfig.price)
        pay_to: 收款方(ResourceConfig.pay_to)
        asset: 资产(USDC 等, 计入审计)
        """
        amount = resource_price_to_amount(price)
        to = pay_to or f"{resource} [{asset}]".strip()
        action = f"{self.action_prefix}:{resource}"
        if max_amount > 0 and amount > max_amount:
            from ..guard import BudgetExceeded
            raise BudgetExceeded(f"[单次上限] {action} ¥{amount} 超过 ¥{max_amount}")
        # 走全部闸门(dry_run/黑名单/频率/预算/审批), 身份+意图
        self.guard._authorize(action, amount, to, agent=self.agent)
        return True

    def confirm_payment(self, resource: str, price: Any, pay_to: str = "") -> None:
        """结算成功后调用: 累计已花 + 登记收款方(预算闸门持续生效)。"""
        amount = resource_price_to_amount(price)
        to = pay_to or resource
        self.guard.book(agent=self.agent, amount=amount, to=to)


def confirm_x402_payment(guard: SpendShield, amount: float, to: str, agent: str = "") -> None:
    """客户端: 支付成功后确认(累计已花 + 登记收款方)。"""
    guard.book(agent=agent, amount=amount, to=to)


def protect_x402_payment(guard: SpendShield, amount: float, to: str,
                         action: str = "x402 支付", agent: str = "",
                         max_amount: float = 0.0) -> bool:
    """客户端: 发起 x402 支付前先过闸门。

    防提示注入的 agent 未经授权乱付钱:
    - 新收款方默认需要人工确认(意图一致性)
    - 预算/黑名单/频率照常生效
    - 未注册 agent 默认拒绝(KYA)

    返回 True = 可继续支付; 被拦抛 GuardedError(不要发起支付)。
    """
    self_guard = guard
    if max_amount > 0 and amount > max_amount:
        from ..guard import BudgetExceeded
        raise BudgetExceeded(f"[单次上限] {action} ¥{amount} 超过 ¥{max_amount}")
    self_guard._authorize(action, amount, to, agent=agent)
    return True
