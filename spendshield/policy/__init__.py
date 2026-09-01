# -*- coding: utf-8 -*-
"""
SpendShield V2 Policy Engine — 公开 API

    from spendshield.policy import Policy, PaymentRequest, PolicySimulator
"""
from .engine import evaluate
from .schema import (
    AgentPolicy,
    ApprovalRule,
    AuthorizationResult,
    BudgetRule,
    EngineState,
    MerchantRule,
    PaymentRequest,
    Policy,
    RateLimitRule,
    RuleHit,
    TransactionRule,
)
from .simulator import PolicySimulator
from .validator import PolicyValidationError, load_policy, validate_raw
from .versioning import diff, list_versions, load_version, rollback, snapshot

__all__ = [
    "Policy", "AgentPolicy", "PaymentRequest", "AuthorizationResult", "RuleHit",
    "BudgetRule", "TransactionRule", "MerchantRule", "ApprovalRule", "RateLimitRule",
    "EngineState", "evaluate",
    "load_policy", "validate_raw", "PolicyValidationError",
    "PolicySimulator",
    "snapshot", "list_versions", "load_version", "rollback", "diff",
]
