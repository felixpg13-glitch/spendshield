# -*- coding: utf-8 -*-
"""
SpendShield V2.1 — Policy Simulator

在 Agent 真花钱之前模拟: 单点评估 / 金额扫描找边界 / 规则矩阵。
用独立 EngineState, 不污染真实运行状态。
"""
from __future__ import annotations

from typing import Optional

from .engine import evaluate, _norm_merchant as _norm_to
from .schema import AgentPolicy, AuthorizationResult, EngineState, PaymentRequest
from .validator import load_policy


class PolicySimulator:
    def __init__(self, policy_raw: Optional[dict] = None, policy=None, agents: Optional[dict] = None):
        """
        policy_raw: YAML 解析后的 dict; 或直接传已构建的 Policy
        agents:     agents 配置表 {agent_id: {...}} 用于合并(与 guard 层一致)
        """
        if policy is None:
            policy = load_policy(policy_raw or {})
        self.policy = policy
        # agents 配置表: 优先显式传入, 否则从 policy_raw 的 agents 段提取
        if agents is None:
            agents = (policy_raw or {}).get("agents", {})
        self.agents = agents or {}
        self.state = EngineState()

    def _merged(self, agent: str) -> AgentPolicy:
        return AgentPolicy.merge(agent, self.policy, self.agents.get(agent))

    def evaluate(self, agent: str, amount: float, to: str, **meta) -> AuthorizationResult:
        req = PaymentRequest(agent=agent, amount=amount, to=to, meta=meta)
        res = evaluate(req, self._merged(agent), self.state)
        if res.decision == "ALLOW":
            # 模拟真实 guard 行为: 放行后记账(预算累计 / 频率窗口 / 收款方记忆)
            from datetime import date, datetime
            day = date.today().isoformat()
            month = datetime.now().strftime("%Y-%m")
            self.state.spent_total += amount
            self.state.spent_daily[day] = self.state.spent_daily.get(day, 0.0) + amount
            self.state.spent_monthly[month] = self.state.spent_monthly.get(month, 0.0) + amount
            if agent:
                self.state.spent_by_agent[agent] = self.state.spent_by_agent.get(agent, 0.0) + amount
            import time
            self.state.rate_hits.append((time.time(), agent, _norm_to(to, self.policy.merchants.allow_subdomains), amount))
            self.state.known_recipients.add(_norm_to(to, self.policy.merchants.allow_subdomains))
        return res

    def sweep(self, agent: str, to: str, amounts: list[float], reset: bool = True) -> dict:
        """金额扫描: 每个金额独立评估找边界(reset=True 默认, 每个金额从干净 state 开始);
        传 reset=False 则按顺序连续执行(模拟真实连续消费)。"""
        out = {}
        for amt in amounts:
            if reset:
                self.state = EngineState()
            r = self.evaluate(agent, amt, to)
            out[amt] = r.decision
        return out

    def matrix(self, agent: str = "test-agent", to: str = "amazon.com") -> list[dict]:
        """规则矩阵: 列出每条规则 + 一个触发它的测试用例建议。"""
        rows = []
        ap = self._merged(agent)
        if ap.transaction.max > 0:
            rows.append({"rule": "max_transaction", "trigger": f"amount={ap.transaction.max + 1}",
                         "expect": "DENY", "note": f"limit ${ap.transaction.max}"})
        if ap.transaction.min > 0:
            rows.append({"rule": "min_transaction", "trigger": f"amount={max(0.001, ap.transaction.min - 0.01)}",
                         "expect": "DENY", "note": f"minimum ${ap.transaction.min}"})
        if ap.budget.daily > 0:
            rows.append({"rule": "daily_budget", "trigger": f"amount={ap.budget.daily} (累计)",
                         "expect": "DENY", "note": f"daily ${ap.budget.daily}"})
        if ap.budget.monthly > 0:
            rows.append({"rule": "monthly_budget", "trigger": f"amount={ap.budget.monthly} (累计)",
                         "expect": "DENY", "note": f"monthly ${ap.budget.monthly}"})
        if ap.budget.total > 0:
            rows.append({"rule": "total_budget", "trigger": f"amount={ap.budget.total} (累计)",
                         "expect": "DENY", "note": f"total ${ap.budget.total}"})
        if ap.merchants.blocked:
            rows.append({"rule": "merchant_blocked", "trigger": f"to={ap.merchants.blocked[0]}",
                         "expect": "DENY", "note": "blacklist"})
        if ap.merchants.allowed:
            rows.append({"rule": "merchant_allowed", "trigger": "to=unknown-shop.example.com",
                         "expect": "DENY", "note": "not in whitelist"})
        if ap.approval.over > 0:
            rows.append({"rule": "approval_required(over)", "trigger": f"amount={ap.approval.over + 1}",
                         "expect": "APPROVAL" if ap.approval.channel else "DENY", "note": f"threshold ${ap.approval.over}"})
        if ap.approval.new_merchant:
            rows.append({"rule": "approval_required(new_merchant)", "trigger": "to=brand-new-merchant.com",
                         "expect": "APPROVAL" if ap.approval.channel else "DENY", "note": "unknown recipient"})
        if ap.rate_limit.max_calls > 0:
            rows.append({"rule": "rate_limit_calls", "trigger": f"{ap.rate_limit.max_calls + 1}x within {ap.rate_limit.window_s}s",
                         "expect": "DENY", "note": f"max {ap.rate_limit.max_calls} calls"})
        return rows
