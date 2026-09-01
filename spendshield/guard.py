# -*- coding: utf-8 -*-
"""
SpendShield 核心: 四道闸门实现
"""
from __future__ import annotations

import functools
import inspect
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

try:  # V2 Policy Engine(可选依赖, 旧环境无 policy/ 包也能 import guard)
    from .policy import (
        AgentPolicy as V2AgentPolicy,
        AuthorizationResult,
        EngineState as V2EngineState,
        PaymentRequest as V2PaymentRequest,
        evaluate as v2_evaluate,
        load_policy as v2_load_policy,
        PolicyValidationError,
    )
except ImportError:  # pragma: no cover
    V2AgentPolicy = AuthorizationResult = V2EngineState = V2PaymentRequest = None
    v2_evaluate = v2_load_policy = None
    PolicyValidationError = None


class GuardedError(Exception):
    """SpendShield 拦截的基础异常"""


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


class SpendShield:
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
        policy: Optional[str] = None,          # 策略文件路径(spendshield.yaml)
        tg_token: str = "",                    # 远程审批: TG bot token
        tg_chat: str = "",                     # 远程审批: TG chat id
        webhook_url: str = "",                 # 远程审批: Webhook URL
        agents: Optional[dict] = None,         # Agent 身份层: {agent_id: {budget/max_amount/blacklist/...}}
        allow_unknown: bool = False,           # 未注册 agent 是否回落全局策略(安全默认拒绝)
        approve_new_recipient: bool = True,    # 意图一致性: 新收款方首次交易强制审批(防提示注入); 未配置审批通道则默认拒绝
        approve_above: float = 0.0,            # 意图一致性: 超过该金额的转账强制审批(0 = 不限)
        key_vault: Optional[Any] = None,       # 密钥保险库(KeyVault 实例): get_secret 过闸门才能取
    ):
        self.budget = budget
        self.dry_run = dry_run
        self.approval = approval
        self.on_block = on_block
        self.log = log or (lambda rec: print(f"[SpendShield] {rec.decision}: {rec.action} ¥{rec.amount} -> {rec.to}"))
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
        self.approve_new_recipient = approve_new_recipient
        self.approve_above = approve_above
        self.vault = key_vault
        self._known_recipients: set[str] = set()   # 成功交易过的收款方(意图一致性记忆)
        # ── V2 Policy Engine 状态(仅在使用新格式 policy 时启用) ──
        self._v2_policy = None            # V2 Policy 对象
        self._v2_agents: dict = {}        # agents 配置表
        self._v2_estate = V2EngineState() if V2EngineState else None
        self._v2_lock = __import__("threading").RLock()   # 评估-记账原子性(防 TOCTOU)
        self._v2_replay: dict = {}        # idempotency_key -> 已执行请求(fingerprint)
        self._v2_replay_decisions: dict = {}   # fingerprint -> decision(防 double spend)
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

        # 2.5/3 审批: 白名单跳过; 否则:
        #   - 意图一致性(防提示注入): 敏感操作(新收款方/大额)强制审批; 未配置审批通道 → 直接拒绝(安全默认)
        #   - 全局 approval 配置: 每笔都问(强模式)
        if not any(w in to_l for w in wl):
            sensitive = False
            sensitive_reason = ""
            if self.approve_new_recipient and to_l not in self._known_recipients:
                sensitive, sensitive_reason = True, f"新收款方需确认: {to}"
            elif self.approve_above > 0 and amount > self.approve_above:
                sensitive, sensitive_reason = True, f"金额 ¥{amount:.2f} > 敏感阈值 ¥{self.approve_above:.2f}, 需确认"
            if sensitive:
                if appr is None:
                    rec = self._record(action=action, amount=amount, to=to, agent=agent,
                                       decision="blocked_approval",
                                       reason=sensitive_reason + ", 未配置审批通道, 安全默认拒绝",
                                       spent_after=self._spent)
                    self.log(rec)
                    if self.on_block:
                        self.on_block(rec)
                    raise NeedsApproval(f"[确认] {action} ¥{amount} -> {to} 未获批准({sensitive_reason}, 未配置审批通道)")
                ok = self._ask(action, amount, to, agent)
                if not ok:
                    rec = self._record(action=action, amount=amount, to=to, agent=agent,
                                       decision="blocked_approval", reason=sensitive_reason + ", 审批被拒",
                                       spent_after=self._spent)
                    self.log(rec)
                    if self.on_block:
                        self.on_block(rec)
                    raise NeedsApproval(f"[确认] {action} ¥{amount} -> {to} 未获批准({sensitive_reason})")
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
            text = f"⚠️  SpendShield 审批\n{who}{action} ¥{amount:.2f} -> {to}\n回复 y 确认 / n 拒绝"
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
        函数签名需能取出金额和收款方: 参数名 amount/price/cost + to/recipient/target,
        或显式传 amount=xx / to=xx(支持位置传参)
        """
        def deco(fn: Callable) -> Callable:
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                ag = kwargs.get("agent") or agent or ""
                amount = self._extract_amount(fn, args, kwargs)
                to = self._extract_to(fn, args, kwargs)
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
                self._known_recipients.add(to.lower())   # 交易成功 → 记为已知收款方
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

    def get_secret(self, name: str, *, action: str = "取密钥", agent: str = "", to: str = "") -> str:
        """密钥保险库取用: 必须先过闸门(身份 + 意图审批), 取用留审计。
        name: 密钥名(视为收款方, 可加白名单免问); 未配置审批通道时新密钥名默认拒绝。"""
        if self.vault is None:
            raise ValueError("未配置 KeyVault: SpendShield(key_vault=KeyVault(...))")
        self._check(action, 0.0, to or name, agent=agent)   # 走身份 + 敏感审批闸门
        secret = self.vault.retrieve(name)
        rec = self._record(action=action, amount=0.0, to=to or name, agent=agent,
                           decision="secret_access", reason=f"密钥 {name} 已取用",
                           spent_after=self._spent)
        self.log(rec)
        return secret

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

    @staticmethod
    def _extract_to(fn: Callable, args: tuple, kwargs: dict) -> str:
        """从参数里提取收款方: 优先显式 to=, 其次参数名 to/recipient/target(支持位置传参)"""
        if "to" in kwargs:
            return str(kwargs["to"])
        sig = inspect.signature(fn)
        names = list(sig.parameters.keys())
        for i, nm in enumerate(names):
            if nm in ("to", "recipient", "target"):
                if i < len(args):
                    return str(args[i])
                if nm in kwargs:
                    return str(kwargs[nm])
        return "(unknown)"

    def load_policy(self, path: str):
        """从 YAML 策略文件加载配置(策略即代码)。

        新格式(V2 Policy Engine): 带 version + policy/agents 段 → 启用 V2 引擎
        旧格式(扁平配置): 保留旧行为 + 自动迁移到 V2(双轨, 不破坏现有调用)
        """
        import yaml
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        # ── V2 新格式检测 ──
        if "policy" in cfg or ("version" in cfg and ("agents" in cfg or "policy" in cfg)):
            self._setup_v2(cfg)
            return self
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
        if cfg.get("approve_new_recipient") is not None:
            self.approve_new_recipient = bool(cfg["approve_new_recipient"])
        if cfg.get("approve_above") is not None:
            self.approve_above = float(cfg["approve_above"])
        if cfg.get("vault"):
            from .vault import KeyVault
            vpath = cfg["vault"].get("path", "spendshield_vault.json")
            venv = cfg["vault"].get("master_key_env", "SPENDGUARD_MASTER_KEY")
            mk = os.environ.get(venv) if venv else None
            if mk:
                self.vault = KeyVault(vpath, master_key=mk)
        for aid, aconf in (cfg.get("agents") or {}).items():
            self.register_agent(aid, **{k: v for k, v in aconf.items()
                                        if k in ("budget", "max_amount", "blacklist",
                                                 "whitelist", "rate_limit", "approval")})
        # ── 旧格式自动迁移到 V2(双轨) ──
        try:
            self._setup_v2(self._migrate_v1(cfg))
        except Exception as e:  # pragma: no cover - 迁移失败不影响旧行为
            print(f"[SpendShield] V2 policy migration skipped: {e}")
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
            "known_recipients": len(self._known_recipients),
            "agents": agents,
        }

    def export_audit(self, path: str = "spendshield_audit.json") -> str:
        """导出审计日志(自动创建父目录)"""
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump([r.to_dict() for r in self.records], f, ensure_ascii=False, indent=2)
        return path

    # ═══════════════════════════════════════════════════════════════
    # V2 Policy Engine — 新 API(不抛异常, 返回 AuthorizationResult)
    # ═══════════════════════════════════════════════════════════════
    @staticmethod
    def _migrate_v1(cfg: dict) -> dict:
        """旧格式(扁平 YAML) → 新格式。语义保守:
        whitelist 旧语义=信任名单(跳过审批) → 迁移为预信任收款方, 不变成 V2 的「仅允许」。"""
        rl = cfg.get("rate_limit") or {}
        appr_channel = cfg.get("approval") if cfg.get("approval") in ("console", "tg", "webhook", "callable") else ""
        return {
            "version": "1.x-migrated",
            "policy": {
                "budget": {"total": float(cfg.get("budget", 0) or 0)},
                "transaction": {"max": float(cfg.get("max_amount", 0) or 0)},
                "merchants": {
                    "allowed": [],
                    "blocked": [str(x).lower() for x in (cfg.get("blacklist") or [])],
                },
                "approval": {
                    "over": float(cfg.get("approve_above", 0) or 0),
                    "new_merchant": bool(cfg.get("approve_new_recipient", True)),
                    "channel": appr_channel,
                },
                "rate_limit": {
                    "window_s": int(rl.get("window_s", 3600)),
                    "max_calls": int(rl.get("max_calls", 0) or 0),
                },
                "allow_unknown": bool(cfg.get("allow_unknown", False)),
            },
            "agents": cfg.get("agents", {}) or {},
            "_trusted_from_v1": [str(x).lower() for x in (cfg.get("whitelist") or [])],
        }

    def _setup_v2(self, raw: dict) -> None:
        """加载 V2 policy(新格式或迁移后), 校验失败抛异常"""
        if v2_load_policy is None:  # pragma: no cover
            raise RuntimeError("V2 policy engine not available (policy/ package missing)")
        self._v2_policy = v2_load_policy(raw)
        self._v2_agents = raw.get("agents", {}) or {}
        for trusted in raw.get("_trusted_from_v1", []):   # 旧 whitelist → 预信任
            self._v2_estate.known_recipients.add(trusted)
            self._known_recipients.add(trusted)

    def authorize(self, agent: str = "", amount: float = 0.0, to: str = "", meta: Optional[dict] = None,
                  action: str = "") -> AuthorizationResult:
        """V2 授权入口: 返回 AuthorizationResult, 不抛异常。

        决策 ALLOW 后自动记账(预算/频率/收款方记忆), 全部在锁内完成(防并发 TOCTOU)。
        meta 带 idempotency_key 时防重放: 同一 key 已成功 → 拒绝(防 double spend)。
        """
        if self._v2_policy is None:
            raise RuntimeError("no V2 policy loaded: use load_policy() with new-format YAML first")
        from .policy import PaymentRequest as PR
        meta = dict(meta or {})
        req = PR(agent=agent or "", amount=float(amount), to=to, meta=meta)
        # 未知 agent 安全默认
        if agent and agent not in self._v2_agents and not self._v2_policy.allow_unknown:
            return self._v2_result("DENY", f"unknown agent '{agent}', denied by default", req)
        ap = V2AgentPolicy.merge(agent or "", self._v2_policy, self._v2_agents.get(agent))
        with self._v2_lock:
            # 防重放: 幂等键已消费过 → 拒绝(同 key 任何内容变体都算重放)
            ikey = meta.get("idempotency_key")
            if ikey and ikey in self._v2_replay:
                res = self._v2_result("DENY", f"replay detected: idempotency_key '{ikey}' already executed", req)
                self._record_v2(res, action=action or "authorize")
                return res
            res = v2_evaluate(req, ap, self._v2_estate)
            if res.decision == "ALLOW":
                self._book_v2(agent, amount, to)
                if ikey:
                    self._v2_replay[ikey] = req.to_dict()
            elif res.decision == "APPROVAL":
                self._v2_estate.pending[res.approval_id] = (req, ap)
        # 输出统一脱敏
        if res.request:
            res.request["meta"] = self._redact_meta(res.request.get("meta", {}))
        self._record_v2(res, action=action or "authorize")
        return res

    def _v2_result(self, decision: str, reason: str, req) -> AuthorizationResult:
        """构造结果 + 输出脱敏(request.meta 中敏感键打码)"""
        res = AuthorizationResult(decision=decision, reason=reason,
                                  request=req.to_dict(),
                                  policy_version=self._v2_policy.version if self._v2_policy else "")
        res.request["meta"] = self._redact_meta(res.request.get("meta", {}))
        return res

    @staticmethod
    def _redact_meta(meta: dict) -> dict:
        """审计/日志输出脱敏: secret/token/password/key/card 等键打码"""
        _SENSITIVE = ("secret", "token", "password", "passwd", "api_key", "apikey",
                      "authorization", "cookie", "credit", "card", "cvv", "key")
        out = {}
        for k, v in (meta or {}).items():
            kl = str(k).lower()
            out[k] = "***REDACTED***" if any(s in kl for s in _SENSITIVE) else v
        return out

    def approve(self, approval_id: str, by: str = "") -> AuthorizationResult:
        """批准挂起审批 → 重新评估(防审批期间预算/名单变化)"""
        if self._v2_policy is None:
            raise RuntimeError("no V2 policy loaded")
        with self._v2_lock:
            item = self._v2_estate.pending.pop(approval_id, None)
            if item is None:
                res = AuthorizationResult(decision="DENY", reason=f"unknown approval id '{approval_id}'",
                                          policy_version=self._v2_policy.version)
                self._record_v2(res, action="approve")
                return res
            req, ap = item
            # 人工确认 = 收款方视为可信
            from .policy.engine import _norm_merchant
            self._v2_estate.known_recipients.add(_norm_merchant(req.to, ap.merchants.allow_subdomains))
            res = v2_evaluate(req, ap, self._v2_estate, approval_granted=True)
            if res.decision == "ALLOW":
                self._book_v2(req.agent, req.amount, req.to)
                ikey = req.meta.get("idempotency_key")
                if ikey:
                    self._v2_replay[ikey] = req.to_dict()
            elif res.decision == "APPROVAL":   # 理论不可达(已豁免), 防御
                res.decision = "DENY"
                res.reason = "approval re-requested after grant (state changed), denied"
        if res.request:
            res.request["meta"] = self._redact_meta(res.request.get("meta", {}))
        self._record_v2(res, action=f"approve[{by}]" if by else "approve")
        return res

    def reject(self, approval_id: str, by: str = "") -> AuthorizationResult:
        with self._v2_lock:
            self._v2_estate.pending.pop(approval_id, None)
        res = AuthorizationResult(decision="DENY", reason=f"rejected by {by}" if by else "rejected",
                                  policy_version=self._v2_policy.version if self._v2_policy else "")
        self._record_v2(res, action="reject")
        return res

    def pending_approvals(self) -> list[dict]:
        return [{"approval_id": k, "request": v[0].to_dict()} for k, v in
                (self._v2_estate.pending.items() if self._v2_estate else {})]

    def _book_v2(self, agent: str, amount: float, to: str) -> None:
        """V2 记账(在锁内调用): 预算累计 + 频率窗口 + 收款方记忆 + 旧层审计同步"""
        from datetime import date, datetime
        import time as _t
        day = date.today().isoformat()
        month = datetime.now().strftime("%Y-%m")
        st = self._v2_estate
        st.spent_total += amount
        st.spent_daily[day] = st.spent_daily.get(day, 0.0) + amount
        st.spent_monthly[month] = st.spent_monthly.get(month, 0.0) + amount
        from .policy.engine import _norm_merchant
        nm = _norm_merchant(to, self._v2_policy.merchants.allow_subdomains)
        st.rate_hits.append((_t.time(), agent, nm, amount))
        st.known_recipients.add(nm)
        self._known_recipients.add(nm)   # 与旧层记忆同步
        self._spent += amount            # 旧层总账同步(便于 summary 一致)
        if agent:
            self._agent_spent[agent] = self._agent_spent.get(agent, 0.0) + amount

    def _record_v2(self, res: AuthorizationResult, action: str = "") -> None:
        rec = AuditRecord(action=action, agent=(res.request or {}).get("agent", ""),
                          amount=float((res.request or {}).get("amount", 0) or 0),
                          to=(res.request or {}).get("to", ""),
                          decision=f"v2_{res.decision.lower()}", reason=res.reason,
                          spent_after=self._spent)
        self.records.append(rec)
        self.log(rec)
