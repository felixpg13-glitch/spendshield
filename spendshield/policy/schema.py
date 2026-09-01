# -*- coding: utf-8 -*-
"""
SpendShield V2 Policy Engine — 数据模型
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# 规则命中(Explanation 的原子单位)
# ---------------------------------------------------------------------------
@dataclass
class RuleHit:
    rule: str            # 规则 id: "max_transaction" / "daily_budget" / ...
    operator: str        # ">" | "<" | "in" | "not_in" | ...
    expected: Any        # 规则阈值: 50
    actual: Any          # 实际值: 75
    severity: str        # "info" | "warn" | "block"
    message: str         # 人类可读: "transaction $75 exceeds the $50 limit"

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "operator": self.operator,
            "expected": self.expected,
            "actual": self.actual,
            "severity": self.severity,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# 支付请求
# ---------------------------------------------------------------------------
@dataclass
class PaymentRequest:
    agent: str                 # Agent ID
    amount: float              # 金额(正数)
    to: str                    # 收款方(商户/地址/账号)
    meta: dict = field(default_factory=dict)   # 附加上下文: order_id / currency / intent...

    def to_dict(self) -> dict:
        return {"agent": self.agent, "amount": self.amount, "to": self.to, "meta": self.meta}


# ---------------------------------------------------------------------------
# 授权结果
# ---------------------------------------------------------------------------
@dataclass
class AuthorizationResult:
    decision: str              # "ALLOW" | "DENY" | "APPROVAL"
    reason: str                # 人类可读(给用户/LLM)
    rules: list = field(default_factory=list)          # [RuleHit]
    approval_id: str = ""      # APPROVAL 时生成
    request: Optional[dict] = None
    policy_version: str = ""
    ts: float = field(default_factory=time.time)
    audit_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "rules": [r.to_dict() if hasattr(r, "to_dict") else r for r in self.rules],
            "approval_id": self.approval_id,
            "request": self.request,
            "policy_version": self.policy_version,
            "ts": self.ts,
            "audit_id": self.audit_id,
        }

    def __repr__(self) -> str:  # 终端可读, 不需要用户再调 to_dict
        head = f"{'✅' if self.decision == 'ALLOW' else '❌' if self.decision == 'DENY' else '⏸️'} {self.decision}"
        lines = [head, f"Reason: {self.reason}"]
        for r in self.rules:
            sev = "" if r.severity == "info" else f" ({r.severity})"
            lines.append(f"  - {r.rule}: {r.message}{sev}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Policy 数据模型(与 YAML 一一对应)
# ---------------------------------------------------------------------------
@dataclass
class BudgetRule:
    daily: float = 0.0
    monthly: float = 0.0
    total: float = 0.0


@dataclass
class TransactionRule:
    max: float = 0.0
    min: float = 0.0


@dataclass
class MerchantRule:
    allowed: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    allow_subdomains: bool = True


@dataclass
class ApprovalRule:
    over: float = 0.0
    new_merchant: bool = True
    channel: str = ""          # console / tg / webhook / callable


@dataclass
class RateLimitRule:
    window_s: int = 3600
    max_calls: int = 0
    max_total: float = 0.0


@dataclass
class Policy:
    version: str
    budget: BudgetRule = field(default_factory=BudgetRule)
    transaction: TransactionRule = field(default_factory=TransactionRule)
    merchants: MerchantRule = field(default_factory=MerchantRule)
    approval: ApprovalRule = field(default_factory=ApprovalRule)
    rate_limit: RateLimitRule = field(default_factory=RateLimitRule)
    allow_unknown: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "Policy":
        p = d.get("policy", d)
        b = p.get("budget", {}) or {}
        t = p.get("transaction", {}) or {}
        m = p.get("merchants", {}) or {}
        a = p.get("approval", {}) or {}
        r = p.get("rate_limit", {}) or {}
        return cls(
            version=str(d.get("version", "")),
            budget=BudgetRule(
                daily=float(b.get("daily", 0) or 0),
                monthly=float(b.get("monthly", 0) or 0),
                total=float(b.get("total", 0) or 0),
            ),
            transaction=TransactionRule(
                max=float(t.get("max", 0) or 0),
                min=float(t.get("min", 0) or 0),
            ),
            merchants=MerchantRule(
                allowed=[str(x).lower() for x in (m.get("allowed") or [])],
                blocked=[str(x).lower() for x in (m.get("blocked") or [])],
                allow_subdomains=bool(m.get("allow_subdomains", True)),
            ),
            approval=ApprovalRule(
                over=float(a.get("over", 0) or 0),
                new_merchant=bool(a.get("new_merchant", True)),
                channel=str(a.get("channel", "") or ""),
            ),
            rate_limit=RateLimitRule(
                window_s=int(r.get("window_s", 3600)),
                max_calls=int(r.get("max_calls", 0) or 0),
                max_total=float(r.get("max_total", 0) or 0),
            ),
            allow_unknown=bool(p.get("allow_unknown", d.get("allow_unknown", False))),
        )


@dataclass
class AgentPolicy:
    """合并后的单 agent 生效策略(agent 级 ⊳ 全局级, 写了就整段覆盖)"""
    agent: str
    version: str = ""
    budget: BudgetRule = field(default_factory=BudgetRule)
    transaction: TransactionRule = field(default_factory=TransactionRule)
    merchants: MerchantRule = field(default_factory=MerchantRule)
    approval: ApprovalRule = field(default_factory=ApprovalRule)
    rate_limit: RateLimitRule = field(default_factory=RateLimitRule)
    allow_unknown: bool = False

    @classmethod
    def merge(cls, agent: str, global_p: Policy, agent_cfg: Optional[dict]) -> "AgentPolicy":
        if not agent_cfg:
            return cls(agent=agent, version=global_p.version, budget=global_p.budget, transaction=global_p.transaction,
                       merchants=global_p.merchants, approval=global_p.approval,
                       rate_limit=global_p.rate_limit, allow_unknown=global_p.allow_unknown)
        b = agent_cfg.get("budget", {}) or {}
        t = agent_cfg.get("transaction", {}) or {}
        m = agent_cfg.get("merchants", {}) or {}
        a = agent_cfg.get("approval", {}) or {}
        r = agent_cfg.get("rate_limit", {}) or {}

        def pick(section_cfg: dict, global_obj, field_map: dict):
            # agent 写了该段 → 整段覆盖(数值用 agent 的, 没写的字段用全局); 没写 → 全局
            data = {}
            for k, attr in field_map.items():
                data[k] = section_cfg.get(k, getattr(global_obj, attr, 0))
            return data

        return cls(
            agent=agent, version=global_p.version,
            budget=BudgetRule(**pick(b, global_p.budget, {"daily": "daily", "monthly": "monthly", "total": "total"})),
            transaction=TransactionRule(**pick(t, global_p.transaction, {"max": "max", "min": "min"})),
            merchants=MerchantRule(
                allowed=[str(x).lower() for x in (m.get("allowed", global_p.merchants.allowed))],
                blocked=[str(x).lower() for x in (m.get("blocked", global_p.merchants.blocked))],
                allow_subdomains=m.get("allow_subdomains", global_p.merchants.allow_subdomains),
            ),
            approval=ApprovalRule(
                over=float(a.get("over", global_p.approval.over)),
                new_merchant=a.get("new_merchant", global_p.approval.new_merchant),
                channel=a.get("channel", global_p.approval.channel) or "",
            ),
            rate_limit=RateLimitRule(
                window_s=int(r.get("window_s", global_p.rate_limit.window_s)),
                max_calls=int(r.get("max_calls", global_p.rate_limit.max_calls)),
                max_total=float(r.get("max_total", global_p.rate_limit.max_total)),
            ),
            allow_unknown=bool(agent_cfg.get("allow_unknown", global_p.allow_unknown)),
        )


# ---------------------------------------------------------------------------
# 运行时状态(引擎纯函数, 状态由调用方持有)
# ---------------------------------------------------------------------------
@dataclass
class EngineState:
    spent_total: float = 0.0
    spent_daily: dict = field(default_factory=dict)       # { "YYYY-MM-DD": amount }
    spent_monthly: dict = field(default_factory=dict)     # { "YYYY-MM": amount }
    rate_hits: list = field(default_factory=list)         # [(ts, agent, to_lower, amount)]
    known_recipients: set = field(default_factory=set)    # 成功交易过的收款方(小写)
    pending: dict = field(default_factory=dict)           # { approval_id: PaymentRequest }
