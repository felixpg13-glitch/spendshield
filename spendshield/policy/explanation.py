# -*- coding: utf-8 -*-
"""
SpendShield Explainability — 决策解释层

三层解释:
  1. decision + reason    人类可读一句话
  2. rules[].code        稳定机器码(MAX_TRANSACTION_EXCEEDED 等, LLM/UI/审计可编程消费)
  3. explain()           自然语言段落(审计报告/企业用户)

code 是稳定契约, 不随文案变化; rule id 是内部标识。
"""
from __future__ import annotations

# rule id → 稳定机器码(新规则必须登记, 保持唯一)
RULE_CODES = {
    "merchant_blocked": "MERCHANT_BLOCKED",
    "merchant_allowed": "MERCHANT_NOT_ALLOWED",
    "max_transaction": "MAX_TRANSACTION_EXCEEDED",
    "min_transaction": "MIN_TRANSACTION_NOT_MET",
    "total_budget": "TOTAL_BUDGET_EXCEEDED",
    "daily_budget": "DAILY_BUDGET_EXCEEDED",
    "monthly_budget": "MONTHLY_BUDGET_EXCEEDED",
    "rate_limit_calls": "RATE_LIMIT_CALLS_EXCEEDED",
    "rate_limit_total": "RATE_LIMIT_TOTAL_EXCEEDED",
    "approval_required": "APPROVAL_REQUIRED",
    "valid_amount": "INVALID_AMOUNT",
    "valid_merchant": "INVALID_MERCHANT",
}

# 致命规则(block)按此顺序决定主要 DENY 原因
_BLOCK_PRIORITY = [
    "valid_amount", "valid_merchant",
    "merchant_blocked", "merchant_allowed",
    "max_transaction", "min_transaction",
    "total_budget", "daily_budget", "monthly_budget",
    "rate_limit_calls", "rate_limit_total",
    "approval_required",
]


def rule_code(rule_id: str) -> str:
    return RULE_CODES.get(rule_id, rule_id.upper())


def primary_rule(hits: list) -> dict:
    """从命中规则里挑「主因」(按致命优先级), 供审计/展示用"""
    if not hits:
        return {}
    for rid in _BLOCK_PRIORITY:
        for h in hits:
            if getattr(h, "rule", None) == rid:
                return {"rule": h.rule, "code": rule_code(h.rule), "message": h.message}
    h = hits[0]
    return {"rule": h.rule, "code": rule_code(h.rule), "message": h.message}


def explain(result) -> str:
    """自然语言解释段落(给审计报告/企业用户)"""
    lines = [f"Decision: {result.decision}"]
    if result.reason:
        lines.append(f"Reason: {result.reason}")
    for h in (result.rules or []):
        sev = "" if h.severity == "info" else f" ({h.severity})"
        lines.append(f"  - {rule_code(h.rule)}: {h.message}{sev}")
    if result.policy_version:
        lines.append(f"Policy version: {result.policy_version}")
    if getattr(result, "approval_id", ""):
        lines.append(f"Approval id: {result.approval_id}")
    return "\n".join(lines)
