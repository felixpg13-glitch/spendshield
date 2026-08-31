# -*- coding: utf-8 -*-
"""
SpendGuard 核心: 四道闸门实现
"""
from __future__ import annotations

import functools
import inspect
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional


class GuardedError(Exception):
    """SpendGuard 拦截的基础异常"""


class DryRunBlocked(GuardedError):
    """干跑模式: 动作被预览拦截, 未执行"""


class BudgetExceeded(GuardedError):
    """预算超限: 本次花费会导致总预算超支"""


class NeedsApproval(GuardedError):
    """需要人工确认: 未获批准, 动作未执行"""


class UnknownAgent(GuardedError):
    """未注册的 Agent 身份: 默认拒绝(安全默认)"""


@dataclass
class AuditRecord:
    """一次被闸门处理的记录"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: float = field(default_factory=time.time)
    action: str = ""
    amount: float = 0.0
    to: str = ""
    agent: str = ""           # 调用方 Agent 身份(KYA 最小实现)
    decision: str = ""       # preview / blocked_budget / blocked_approval / executed / dry_run
    reason: str = ""
    spent_after: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class SpendGuard:
    """
    给花钱函数加闸门。

    参数:
        budget: 总预算(0 = 不限)
        dry_run: 干跑模式, 默认 True。只预览不执行。
        approval: 人工确认模式。None=不需要, "console"=终端输入, 或 callable(record)->bool
        on_block: 拦截回调, 可选
        log: 审计日志回调, 可选(默认打印)
    """

    def __init__(
        self,
        budget: float = 0.0,
        dry_run: bool = True,
        approval: Optional[Any] = None,
        on_block: Optional[Callable[[AuditRecord], None]] = None,
        log: Optional[Callable[[AuditRecord], None]] = None,
        blacklist: Optional[list] = None,      # 收款方黑名单: 直接拒绝
        whitelist: Optional[list] = None,      # 收款方白名单: 跳过人工确认
        rate_limit: Optional[dict] = None,     # {"window_s": 60, "max_calls": 3} 频率限制
        policy: Optional[str] = None,          # 策略文件路径(spendguard.yaml)
        tg_token: str = "",                    # 远程审批: TG bot token
        tg_chat: str = "",                     # 远程审批: TG chat id
        webhook_url: str = "",                 # 远程审批: Webhook URL
        agents: Optional[dict] = None,         # Agent 身份层: {agent_id: {budget/max_amount/blacklist/...}}
        allow_unknown: bool = False,           # 未注册 agent 是否回落全局策略(安全默认拒绝)
    ):
        self.budget = budget
        self.dry_run = dry_run
        self.approval = approval
        self.on_block = on_block
        self.log = log or (lambda rec: print(f"[SpendGuard] {rec.decision}: {rec.action} ¥{rec.amount} -> {rec.to}"))
        self.blacklist = [str(x).lower() for x in (blacklist or [])]
        self.whitelist = [str(x).lower() for x in (whitelist or [])]
        self.rate_limit = rate_limit or {}
        self.tg_token = tg_token
        self.tg_chat = tg_chat
        self.webhook_url = webhook_url
        self._tg_offset = 0
        self.default_max_amount = 0.0
        self._rate_hits: list[tuple] = []      # (ts, agent, to)
        self._spent = 0.0
        self.records: list[AuditRecord] = []
        self._agents: dict[str, dict] = {}
        self._agent_spent: dict[str, float] = {}
        self.allow_unknown = allow_unknown
        for aid, aconf in (agents or {}).items():
            self.register_agent(aid, **{k: v for k, v in aconf.items()
                                        if k in ("budget", "max_amount", "blacklist",
                                                 "whitelist", "rate_limit", "approval")})
        if policy:
            self.load_policy(policy)

    @property
    def spent(self) -> float:
        return self._spent

    def _record(self, **kw) -> AuditRecord:
        rec = AuditRecord(**kw)
        self.records.append(rec)
        return rec

    def _check(self, action: str, amount: float, to: str, agent: str = "") -> AuditRecord:
        """四道闸门, 返回通过的记录(未执行), 抛异常则被拦。
        agent: 调用方身份(Agent ID, KYA 最小实现)。未注册默认拒绝, allow_unknown=True 回落全局策略。"""
        try:
            ap = self._agent_policy(agent)
        except UnknownAgent:
            rec = self._record(action=action, amount=amount, to=to, agent=agent,
                               decision="blocked_unknown_agent", reason="未注册的 Agent 身份, 默认拒绝",
                               spent_after=self._spent)
            self.log(rec)
            if self.on_block:
                self.on_block(rec)
            raise
        # 合并生效策略: agent 级覆盖全局
        bl = self.blacklist + list(ap.get("blacklist", []))
        wl = self.whitelist + list(ap.get("whitelist", []))
        rl = ap.get("rate_limit") or self.rate_limit
        ab = float(ap.get("budget", 0) or 0)
        appr = ap.get("approval") if "approval" in ap else self.approval
        agent_spent = self._agent_spent.get(agent, 0.0) if agent else self._spent

        # 1. dry_run 干跑
        if self.dry_run:
            rec = self._record(action=action, amount=amount, to=to, agent=agent,
                               decision="dry_run", reason="dry_run=True 干跑模式, 未执行",
                               spent_after=self._spent)
            self.log(rec)
            raise DryRunBlocked(f"[干跑] {action} ¥{amount} -> {to} (未执行, 关掉 dry_run 才会真花)")

        # 1.5 黑名单: 直接拒绝
        to_l = str(to).lower()
        if any(b in to_l for b in bl):
            rec = self._record(action=action, amount=amount, to=to, agent=agent,
                               decision="blocked_blacklist", reason=f"收款方在黑名单: {to}",
                               spent_after=self._spent)
            self.log(rec)
            if self.on_block:
                self.on_block(rec)
            raise BudgetExceeded(f"[黑名单] {action} ¥{amount} -> {to} 被拒绝(黑名单收款方)")

        # 1.6 频率限制: 同一 agent+收款方 window 内 max_calls 次
        if rl:
            now = time.time()
            window = rl.get("window_s", 60)
            max_calls = rl.get("max_calls", 3)
            self._rate_hits = [(t, a, r) for t, a, r in self._rate_hits if now - t < window]
            hits = sum(1 for _, a, r in self._rate_hits if r == to_l and a == agent)
            if hits >= max_calls:
                rec = self._record(action=action, amount=amount, to=to, agent=agent,
                                   decision="blocked_rate", reason=f"收款方 {to} {window}s 内超过 {max_calls} 次",
                                   spent_after=self._spent)
                self.log(rec)
                if self.on_block:
                    self.on_block(rec)
                raise BudgetExceeded(f"[频率] {action} ¥{amount} -> {to} 触发频率限制({window}s/{max_calls}次)")
            self._rate_hits.append((now, agent, to_l))

        # 2. 预算: agent 级分闸优先, 全局总闸兜底
        if ab > 0 and agent_spent + amount > ab:
            rec = self._record(action=action, amount=amount, to=to, agent=agent,
                               decision="blocked_budget",
                               reason=f"Agent[{agent}] 已花 ¥{agent_spent:.2f} + ¥{amount:.2f} > 预算 ¥{ab:.2f}",
                               spent_after=self._spent)
            self.log(rec)
            if self.on_block:
                self.on_block(rec)
            raise BudgetExceeded(f"[预算] {action} ¥{amount} 超支: Agent[{agent}] 已花 ¥{agent_spent:.2f}, 预算 ¥{ab:.2f}")
        if self.budget > 0 and self._spent + amount > self.budget:
            rec = self._record(action=action, amount=amount, to=to, agent=agent,
                               decision="blocked_budget",
                               reason=f"已花 ¥{self._spent:.2f} + ¥{amount:.2f} > 总预算 ¥{self.budget:.2f}",
                               spent_after=self._spent)
            self.log(rec)
            if self.on_block:
                self.on_block(rec)
            raise BudgetExceeded(f"[预算] {action} ¥{amount} 超支: 已花 ¥{self._spent:.2f}, 总预算 ¥{self.budget:.2f}")

        # 2.5 白名单: 跳过人工确认
        if any(w in to_l for w in wl):
            pass
        # 3. 人工确认
        elif appr is not None:
            ok = self._ask(action, amount, to, agent)
            if not ok:
                rec = self._record(action=action, amount=amount, to=to, agent=agent,
                                   decision="blocked_approval", reason="人工确认被拒",
                                   spent_after=self._spent)
                self.log(rec)
                if self.on_block:
                    self.on_block(rec)
                raise NeedsApproval(f"[确认] {action} ¥{amount} -> {to} 未获批准")
        return self._record(action=action, amount=amount, to=to, agent=agent,
                            decision="executed", reason="", spent_after=self._spent)

    def _ask(self, action: str, amount: float, to: str, agent: str = "") -> bool:
        who = f"[{agent}] " if agent else ""
        if callable(self.approval):
            return bool(self.approval({"action": action, "amount": amount, "to": to, "agent": agent}))
        if self.approval == "console":
            try:
                ans = input(f"\n⚠️  {who}{action} ¥{amount:.2f} -> {to}\n   确认执行? [y/N] ").strip().lower()
                return ans in ("y", "yes")
            except EOFError:
                return False
        if self.approval == "tg":
            return self._ask_tg(action, amount, to, agent=agent)
        if self.approval == "webhook":
            return self._ask_webhook(action, amount, to, agent=agent)
        return False  # 未知模式 = 拒绝(安全默认)

    def _ask_tg(self, action: str, amount: float, to: str, timeout_s: int = 60, agent: str = "") -> bool:
        """TG 远程审批: 发消息等回复 y/n"""
        import urllib.request
        if not self.tg_token or not self.tg_chat:
            return False
        try:
            who = f"[{agent}] " if agent else ""
            text = f"⚠️  SpendGuard 审批\n{who}{action} ¥{amount:.2f} -> {to}\n回复 y 确认 / n 拒绝"
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            body = json.dumps({"chat_id": self.tg_chat, "text": text}).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
            # 轮询等回复
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                up_url = f"https://api.telegram.org/bot{self.tg_token}/getUpdates?timeout=10&offset={self._tg_offset}"
                try:
                    with urllib.request.urlopen(up_url, timeout=15) as r:
                        updates = json.loads(r.read()).get("result", [])
                except Exception:
                    updates = []
                for u in updates:
                    self._tg_offset = u["update_id"] + 1
                    msg_text = ((u.get("message") or {}).get("text") or "").strip().lower()
                    if msg_text in ("y", "yes", "确认", "同意"):
                        return True
                    if msg_text in ("n", "no", "拒绝", "取消"):
                        return False
                time.sleep(1)
            return False
        except Exception:
            return False

    def _ask_webhook(self, action: str, amount: float, to: str, timeout_s: int = 15, agent: str = "") -> bool:
        """Webhook 远程审批: POST 到审核服务, 等 {approved: bool}"""
        import urllib.request
        if not self.webhook_url:
            return False
        try:
            body = json.dumps({"action": action, "amount": amount, "to": to, "agent": agent}).encode()
            req = urllib.request.Request(self.webhook_url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                resp = json.loads(r.read().decode("utf-8", "replace"))
            return bool(resp.get("approved"))
        except Exception:
            return False

    def protect(self, action: str, max_amount: float = 0.0, agent: str = ""):
        """
        装饰器: 给花钱函数加闸门。
        max_amount: 单次上限(0 = 不限)
        agent: Agent 身份 ID(建议必填, 未注册默认拒绝)。也可运行时传 kwargs agent=xx
        函数签名需能取出金额: 参数名含 amount/price/cost/金额, 或显式传 amount=xx
        """
        def deco(fn: Callable) -> Callable:
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                ag = kwargs.get("agent") or agent or ""
                amount = self._extract_amount(fn, args, kwargs)
                to = str(kwargs.get("to", "(unknown)"))
                if "agent" in kwargs and "agent" not in inspect.signature(fn).parameters:
                    kwargs.pop("agent")   # 不透传给业务函数
                if max_amount > 0 and amount > max_amount:
                    rec = self._record(action=action, amount=amount, to=to, agent=ag,
                                       decision="blocked_budget",
                                       reason=f"单次 ¥{amount:.2f} > 上限 ¥{max_amount:.2f}",
                                       spent_after=self._spent)
                    self.log(rec)
                    if self.on_block:
                        self.on_block(rec)
                    raise BudgetExceeded(f"[单次上限] {action} ¥{amount} 超过 ¥{max_amount}")
                # 通过闸门(执行前记录, 执行后更新已花)
                rec = self._check(action, amount, to, agent=ag)
                try:
                    result = fn(*args, **kwargs)
                except Exception as e:
                    rec.decision = "failed"
                    rec.reason = str(e)[:120]
                    self.log(rec)
                    raise
                self._spent += amount
                if ag:
                    self._agent_spent[ag] = self._agent_spent.get(ag, 0.0) + amount
                rec.spent_after = self._spent
                self.log(rec)
                return result
            return wrapper
        return deco

    @staticmethod
    def _extract_amount(fn: Callable, args: tuple, kwargs: dict) -> float:
        """从参数里找金额: 优先显式 amount=, 其次参数名含 amount/price/cost/价"""
        if "amount" in kwargs:
            return float(kwargs["amount"])
        sig = inspect.signature(fn)
        names = list(sig.parameters.keys())
        for i, nm in enumerate(names):
            if any(k in nm.lower() for k in ("amount", "price", "cost")):
                if i < len(args):
                    return float(args[i])
                if nm in kwargs:
                    return float(kwargs[nm])
        return 0.0

    def register_agent(self, agent_id: str, *, budget: float = 0.0, max_amount: float = 0.0,
                       blacklist: Optional[list] = None, whitelist: Optional[list] = None,
                       rate_limit: Optional[dict] = None, approval: Optional[Any] = None) -> None:
        """注册 Agent 身份及其专属策略(KYA 最小实现)。未注册的 agent 调用默认被拒。"""
        if not agent_id:
            raise ValueError("agent_id 不能为空")
        self._agents[agent_id] = {
            "budget": float(budget or 0),
            "max_amount": float(max_amount or 0),
            "blacklist": [str(x).lower() for x in (blacklist or [])],
            "whitelist": [str(x).lower() for x in (whitelist or [])],
            "rate_limit": rate_limit or {},
            "approval": approval,
        }

    def _agent_policy(self, agent_id: str) -> dict:
        """解析 Agent 身份: 未注册默认拒绝(安全默认), allow_unknown=True 回落全局策略"""
        if not agent_id:
            return {}
        if agent_id not in self._agents:
            if self.allow_unknown:
                return {}
            raise UnknownAgent(f"未注册的 Agent 身份: {agent_id!r}(先 register_agent 或 allow_unknown=True)")
        return self._agents[agent_id]

    def load_policy(self, path: str):
        """从 YAML 策略文件加载配置(策略即代码)"""
        import yaml
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        for k in ("budget", "dry_run", "approval"):
            if k in cfg:
                setattr(self, k, cfg[k])
        if "max_amount" in cfg:
            self.default_max_amount = cfg["max_amount"]
        if cfg.get("blacklist"):
            self.blacklist = [str(x).lower() for x in cfg["blacklist"]]
        if cfg.get("whitelist"):
            self.whitelist = [str(x).lower() for x in cfg["whitelist"]]
        if cfg.get("rate_limit"):
            self.rate_limit = cfg["rate_limit"]
        if cfg.get("tg"):
            self.tg_token = cfg["tg"].get("token", self.tg_token)
            self.tg_chat = cfg["tg"].get("chat", self.tg_chat)
        if cfg.get("webhook_url"):
            self.webhook_url = cfg["webhook_url"]
        if cfg.get("allow_unknown") is not None:
            self.allow_unknown = bool(cfg["allow_unknown"])
        for aid, aconf in (cfg.get("agents") or {}).items():
            self.register_agent(aid, **{k: v for k, v in aconf.items()
                                        if k in ("budget", "max_amount", "blacklist",
                                                 "whitelist", "rate_limit", "approval")})
        return self

    def _authorize(self, action: str, amount: float, to: str, agent: str = "") -> bool:
        """MCP/程序化调用入口: 走全部闸门, 通过返回 True, 被拦抛异常"""
        ap = self._agent_policy(agent)
        amax = float(ap.get("max_amount", 0) or 0) or self.default_max_amount
        if amax > 0 and amount > amax:
            rec = self._record(action=action, amount=amount, to=to, agent=agent,
                               decision="blocked_budget",
                               reason=f"单次 ¥{amount:.2f} > 上限 ¥{amax:.2f}",
                               spent_after=self._spent)
            self.log(rec)
            raise BudgetExceeded(f"[单次上限] {action} ¥{amount} 超过 ¥{amax}")
        self._check(action, amount, to, agent=agent)
        return True

    def summary(self) -> dict:
        agents = {
            k: {
                "budget": v.get("budget", 0),
                "spent": round(self._agent_spent.get(k, 0.0), 2),
                "blocked": sum(1 for r in self.records
                               if r.agent == k and (r.decision.startswith("blocked") or r.decision == "dry_run")),
            }
            for k in self._agents
        }
        return {
            "dry_run": self.dry_run,
            "budget": self.budget,
            "spent": round(self._spent, 2),
            "remaining": round(max(self.budget - self._spent, 0), 2) if self.budget > 0 else None,
            "records": len(self.records),
            "blocked": sum(1 for r in self.records if r.decision.startswith("blocked") or r.decision == "dry_run"),
            "executed": sum(1 for r in self.records if r.decision == "executed"),
            "agents": agents,
        }

    def export_audit(self, path: str = "spendguard_audit.json") -> str:
        """导出审计日志"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump([r.to_dict() for r in self.records], f, ensure_ascii=False, indent=2)
        return path
