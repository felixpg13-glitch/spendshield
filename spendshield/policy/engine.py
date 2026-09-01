# -*- coding: utf-8 -*-
"""
SpendShield V2 Policy Engine — 评估管线(纯函数, 无副作用)

gate 链是插拔式的: V3 加 IntentGate, V4 加 RiskGate 就是新增一个 gate 类。
"""
from __future__ import annotations

import math
import time
from datetime import date, datetime
from typing import Optional

from .schema import (
    AgentPolicy,
    AuthorizationResult,
    EngineState,
    PaymentRequest,
    Policy,
    RuleHit,
)


def _today() -> str:
    return date.today().isoformat()


def _month() -> str:
    return datetime.now().strftime("%Y-%m")


def _norm_merchant(to: str, allow_subdomains: bool) -> str:
    """规范化收款方: 小写、去空白、去协议前缀; 子域名折叠到主域(仅当允许)"""
    s = str(to).strip().lower()
    for prefix in ("https://", "http://", "ftp://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    if s.endswith("/"):
        s = s.rstrip("/")
    if allow_subdomains:
        parts = s.split(".")
        if len(parts) > 2:
            s = ".".join(parts[-2:])   # sub.amazon.com → amazon.com
    return s


def evaluate(req: PaymentRequest, policy: Policy, state: EngineState,
             approval_granted: bool = False) -> AuthorizationResult:
    """评估管线: 返回决策结果, 不改 state(决策后的记账由 guard 负责, 防 TOCTOU)"""
    hits: list[RuleHit] = []
    day = _today()
    month = _month()

    # ── 1. resolve_agent: 未知 agent → DENY(安全默认) ────────────────────
    # agent 是否注册由 guard 层决定(需要 agents 配置表), 这里只处理 allow_unknown 标志
    # ── 2. merge_policy: 由 guard 层传入已合并的 AgentPolicy ──────────────

    # ── 3. gate_merchant ──────────────────────────────────────────────────
    if policy.merchants.blocked:
        nm = _norm_merchant(req.to, policy.merchants.allow_subdomains)
        if any(b in nm for b in policy.merchants.blocked):
            hits.append(RuleHit("merchant_blocked", "in", policy.merchants.blocked, req.to,
                                "block", f"merchant '{req.to}' is blocked"))
            return _finish("DENY", req, policy, hits)

    whitelisted = False
    if policy.merchants.allowed:
        nm = _norm_merchant(req.to, policy.merchants.allow_subdomains)
        # 精确域匹配; 仅 allow_subdomains=True 时允许子域
        # (防 "notamazon.com" 含 "amazon.com" 子串绕过)
        whitelisted = any(nm == a or (policy.merchants.allow_subdomains and nm.endswith("." + a))
                          for a in policy.merchants.allowed)
        if not whitelisted and policy.merchants.allowed:
            hits.append(RuleHit("merchant_allowed", "in", policy.merchants.allowed, req.to,
                                "block", f"merchant '{req.to}' is not in the allowed list"))
            return _finish("DENY", req, policy, hits)

    # ── 3.5 输入合法性(恶意输入前置拦截) ─────────────────────────────────
    if not math.isfinite(req.amount):
        hits.append(RuleHit("valid_amount", "finite", True, req.amount,
                            "block", "amount must be a finite number"))
        return _finish("DENY", req, policy, hits)
    if not str(req.to or "").strip():
        hits.append(RuleHit("valid_merchant", "nonempty", True, req.to,
                            "block", "merchant must not be empty"))
        return _finish("DENY", req, policy, hits)

    # ── 4. gate_amount ────────────────────────────────────────────────────
    if policy.transaction.max > 0 and req.amount > policy.transaction.max:
        hits.append(RuleHit("max_transaction", ">", policy.transaction.max, req.amount,
                            "block",
                            f"transaction ${req.amount:.2f} exceeds the ${policy.transaction.max:.2f} limit"))
        return _finish("DENY", req, policy, hits)
    if req.amount <= 0:
        hits.append(RuleHit("min_transaction", ">", 0, req.amount,
                            "block", "amount must be positive"))
        return _finish("DENY", req, policy, hits)
    if policy.transaction.min > 0 and req.amount < policy.transaction.min:
        hits.append(RuleHit("min_transaction", "<", policy.transaction.min, req.amount,
                            "block", f"transaction ${req.amount:.2f} is below the ${policy.transaction.min:.2f} minimum"))
        return _finish("DENY", req, policy, hits)

    # ── 5. gate_budget(含本轮) ────────────────────────────────────────────
    # 累计基数: agent 自己写了 budget → 用该 agent 的累计(隔离); 继承全局 → 用全局累计
    if policy.budget_owner == "agent" and req.agent:
        spent_total = state.spent_by_agent.get(req.agent, 0.0)
    else:
        spent_total = state.spent_total
    if policy.budget.total > 0 and spent_total + req.amount > policy.budget.total:
        hits.append(RuleHit("total_budget", ">", policy.budget.total, spent_total + req.amount,
                            "block", f"total spent ${spent_total:.2f} + ${req.amount:.2f} exceeds the ${policy.budget.total:.2f} total budget"))
        return _finish("DENY", req, policy, hits)
    if policy.budget.daily > 0 and state.spent_daily.get(day, 0.0) + req.amount > policy.budget.daily:
        hits.append(RuleHit("daily_budget", ">", policy.budget.daily, state.spent_daily.get(day, 0.0) + req.amount,
                            "block", f"daily spend ${state.spent_daily.get(day, 0.0):.2f} + ${req.amount:.2f} exceeds the ${policy.budget.daily:.2f} daily budget"))
        return _finish("DENY", req, policy, hits)
    if policy.budget.monthly > 0 and state.spent_monthly.get(month, 0.0) + req.amount > policy.budget.monthly:
        hits.append(RuleHit("monthly_budget", ">", policy.budget.monthly, state.spent_monthly.get(month, 0.0) + req.amount,
                            "block", f"monthly spend ${state.spent_monthly.get(month, 0.0):.2f} + ${req.amount:.2f} exceeds the ${policy.budget.monthly:.2f} monthly budget"))
        return _finish("DENY", req, policy, hits)

    # ── 6. gate_rate ──────────────────────────────────────────────────────
    now = time.time()
    if policy.rate_limit.max_calls > 0 or policy.rate_limit.max_total > 0:
        window = policy.rate_limit.window_s
        state.rate_hits = [(t, a, r, am) for t, a, r, am in state.rate_hits if now - t < window]
        nm = _norm_merchant(req.to, policy.merchants.allow_subdomains)
        hits_win = [(t, a, r, am) for t, a, r, am in state.rate_hits if r == nm and a == req.agent]
        if policy.rate_limit.max_calls > 0 and len(hits_win) >= policy.rate_limit.max_calls:
            hits.append(RuleHit("rate_limit_calls", ">=", policy.rate_limit.max_calls, len(hits_win),
                                "block", f"{window}s window already has {len(hits_win)} transactions, limit is {policy.rate_limit.max_calls}"))
            return _finish("DENY", req, policy, hits)
        if policy.rate_limit.max_total > 0:
            win_total = sum(am for *_, am in hits_win)
            if win_total + req.amount > policy.rate_limit.max_total:
                hits.append(RuleHit("rate_limit_total", ">", policy.rate_limit.max_total, win_total + req.amount,
                                    "block", f"{window}s window total ${win_total:.2f} + ${req.amount:.2f} exceeds the ${policy.rate_limit.max_total:.2f} window limit"))
                return _finish("DENY", req, policy, hits)
    # ── 7. gate_approval(approval_granted=True 表示该笔已人工确认, 豁免) ──
    nm = _norm_merchant(req.to, policy.merchants.allow_subdomains)
    need_approval = False
    approval_reason = ""
    if not approval_granted:
        if policy.approval.over > 0 and req.amount > policy.approval.over:
            need_approval, approval_reason = True, f"amount ${req.amount:.2f} exceeds the ${policy.approval.over:.2f} approval threshold"
        elif policy.approval.new_merchant and nm not in state.known_recipients and not whitelisted \
            and not _is_trusted(nm, state.trusted_prefixes):
            # 白名单商户视为可信, 跳过新商户审批(但 over 阈值仍生效)
            need_approval, approval_reason = True, f"new merchant '{req.to}' requires approval"
        if need_approval:
            if not policy.approval.channel:
                hits.append(RuleHit("approval_required", "=", "channel", None,
                                    "block", approval_reason + ", no approval channel configured, denied by default"))
                return _finish("DENY", req, policy, hits)
            hits.append(RuleHit("approval_required", "=", "manual", None,
                                "warn", approval_reason))
            return _finish("APPROVAL", req, policy, hits)

    # ── 8. ALLOW ──────────────────────────────────────────────────────────
    # 接近预算上限给 warn(explanation 的价值: ALLOW 也要解释)
    for label, rule_obj, cur in (("daily_budget", policy.budget.daily, state.spent_daily.get(day, 0.0)),
                                 ("monthly_budget", policy.budget.monthly, state.spent_monthly.get(month, 0.0))):
        if rule_obj > 0 and cur + req.amount > rule_obj * 0.8:
            hits.append(RuleHit(label, "approaching", rule_obj, cur + req.amount,
                                "warn", f"{label} at ${cur + req.amount:.2f}, limit ${rule_obj:.2f}"))
            break
    return _finish("ALLOW", req, policy, hits)


def _is_trusted(nm: str, trusted_prefixes: set) -> bool:
    """信任判断: 含 '.' 的信任项(域)用边界匹配(amazon.com 匹配 checkout.amazon.com, 不匹配 notamazon.com);
    否则(中文商户名)保留子串语义(V1 whitelist 兼容)。"""
    for tp in trusted_prefixes:
        if "." in tp:
            if nm == tp or nm.endswith("." + tp):
                return True
        else:
            if tp in nm:
                return True
    return False


def _finish(decision: str, req: PaymentRequest, policy: Policy, hits: list[RuleHit]) -> AuthorizationResult:
    if decision == "DENY":
        reason = hits[0].message if hits else "denied"
    elif decision == "APPROVAL":
        reason = hits[0].message if hits else "approval required"
    else:
        reason = "approved"
    res = AuthorizationResult(
        decision=decision,
        reason=reason,
        rules=hits,
        request=req.to_dict(),
        policy_version=policy.version,
    )
    if decision == "APPROVAL":
        import uuid
        res.approval_id = uuid.uuid4().hex[:12]
    return res
