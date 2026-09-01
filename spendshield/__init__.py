# -*- coding: utf-8 -*-
"""
SpendShield — AI Agent 付款安全层

给 AI Agent 的「花钱动作」加上四道闸门:
  1. dry_run    干跑模式(默认开): 只预览, 不真花
  2. budget     预算上限: 超了直接拒绝
  3. approval   人工确认: 花钱前必须人点头
  4. audit      全量审计: 每次尝试都留痕

血泪背景: 2026-08-09, 我让自动化系统测试下单, 因为 dry 参数没生效,
4 单 99 元真实出码扣款。这个库就是那次事故的产物——
AI 时代, 别让 Agent 替你花钱之前没有闸门。

用法:
    from spendshield import SpendShield

    guard = SpendShield(budget=100, dry_run=True, approval="console")

    @guard.protect("下单", max_amount=50)
    def place_order(order_id, amount, to):
        # ... 真实下单逻辑
        return {"ok": True}
"""
from .guard import SpendShield, GuardedError, BudgetExceeded, NeedsApproval, DryRunBlocked, UnknownAgent, AuditRecord
from .vault import KeyVault

__version__ = "0.8.0"
__all__ = [
    "SpendShield", "GuardedError", "BudgetExceeded",
    "NeedsApproval", "DryRunBlocked", "UnknownAgent", "AuditRecord", "KeyVault",
]
