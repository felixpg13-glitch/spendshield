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


@dataclass
class AuditRecord:
    """一次被闸门处理的记录"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: float = field(default_factory=time.time)
    action: str = ""
    amount: float = 0.0
    to: str = ""
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
    ):
        self.budget = budget
        self.dry_run = dry_run
        self.approval = approval
        self.on_block = on_block
        self.log = log or (lambda rec: print(f"[SpendGuard] {rec.decision}: {rec.action} ¥{rec.amount} -> {rec.to}"))
        self.blacklist = [str(x).lower() for x in (blacklist or [])]
        self.whitelist = [str(x).lower() for x in (whitelist or [])]
        self.rate_limit = rate_limit or {}
        self.default_max_amount = 0.0
        self._rate_hits: list[tuple] = []      # (ts, to)
        self._spent = 0.0
        self.records: list[AuditRecord] = []
        if policy:
            self.load_policy(policy)

    @property
    def spent(self) -> float:
        return self._spent

    def _record(self, **kw) -> AuditRecord:
        rec = AuditRecord(**kw)
        self.records.append(rec)
        return rec

    def _check(self, action: str, amount: float, to: str) -> AuditRecord:
        """四道闸门, 返回通过的记录(未执行), 抛异常则被拦"""
        # 1. dry_run 干跑
        if self.dry_run:
            rec = self._record(action=action, amount=amount, to=to,
                               decision="dry_run", reason="dry_run=True 干跑模式, 未执行",
                               spent_after=self._spent)
            self.log(rec)
            raise DryRunBlocked(f"[干跑] {action} ¥{amount} -> {to} (未执行, 关掉 dry_run 才会真花)")

        # 1.5 黑名单: 直接拒绝
        to_l = str(to).lower()
        if any(b in to_l for b in self.blacklist):
            rec = self._record(action=action, amount=amount, to=to,
                               decision="blocked_blacklist", reason=f"收款方在黑名单: {to}",
                               spent_after=self._spent)
            self.log(rec)
            if self.on_block:
                self.on_block(rec)
            raise BudgetExceeded(f"[黑名单] {action} ¥{amount} -> {to} 被拒绝(黑名单收款方)")

        # 1.6 频率限制: 同一收款方 window 内 max_calls 次
        if self.rate_limit:
            now = time.time()
            window = self.rate_limit.get("window_s", 60)
            max_calls = self.rate_limit.get("max_calls", 3)
            self._rate_hits = [(t, r) for t, r in self._rate_hits if now - t < window]
            hits = sum(1 for _, r in self._rate_hits if r == to_l)
            if hits >= max_calls:
                rec = self._record(action=action, amount=amount, to=to,
                                   decision="blocked_rate", reason=f"收款方 {to} {window}s 内超过 {max_calls} 次",
                                   spent_after=self._spent)
                self.log(rec)
                if self.on_block:
                    self.on_block(rec)
                raise BudgetExceeded(f"[频率] {action} ¥{amount} -> {to} 触发频率限制({window}s/{max_calls}次)")
            self._rate_hits.append((now, to_l))

        # 2. 预算
        if self.budget > 0 and self._spent + amount > self.budget:
            rec = self._record(action=action, amount=amount, to=to,
                               decision="blocked_budget",
                               reason=f"已花 ¥{self._spent:.2f} + ¥{amount:.2f} > 预算 ¥{self.budget:.2f}",
                               spent_after=self._spent)
            self.log(rec)
            if self.on_block:
                self.on_block(rec)
            raise BudgetExceeded(f"[预算] {action} ¥{amount} 超支: 已花 ¥{self._spent:.2f}, 预算 ¥{self.budget:.2f}")

        # 2.5 白名单: 跳过人工确认
        if any(w in to_l for w in self.whitelist):
            pass
        # 3. 人工确认
        elif self.approval is not None:
            ok = self._ask(action, amount, to)
            if not ok:
                rec = self._record(action=action, amount=amount, to=to,
                                   decision="blocked_approval", reason="人工确认被拒",
                                   spent_after=self._spent)
                self.log(rec)
                if self.on_block:
                    self.on_block(rec)
                raise NeedsApproval(f"[确认] {action} ¥{amount} -> {to} 未获批准")
        return self._record(action=action, amount=amount, to=to,
                            decision="executed", reason="", spent_after=self._spent)

    def _ask(self, action: str, amount: float, to: str) -> bool:
        if callable(self.approval):
            return bool(self.approval({"action": action, "amount": amount, "to": to}))
        if self.approval == "console":
            try:
                ans = input(f"\n⚠️  {action} ¥{amount:.2f} -> {to}\n   确认执行? [y/N] ").strip().lower()
                return ans in ("y", "yes")
            except EOFError:
                return False
        return False  # 未知模式 = 拒绝(安全默认)

    def protect(self, action: str, max_amount: float = 0.0):
        """
        装饰器: 给花钱函数加闸门。
        max_amount: 单次上限(0 = 不限)
        函数签名需能取出金额: 参数名含 amount/price/cost/金额, 或显式传 amount=xx
        """
        def deco(fn: Callable) -> Callable:
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                amount = self._extract_amount(fn, args, kwargs)
                if max_amount > 0 and amount > max_amount:
                    rec = self._record(action=action, amount=amount, to="(unknown)",
                                       decision="blocked_budget",
                                       reason=f"单次 ¥{amount:.2f} > 上限 ¥{max_amount:.2f}",
                                       spent_after=self._spent)
                    self.log(rec)
                    if self.on_block:
                        self.on_block(rec)
                    raise BudgetExceeded(f"[单次上限] {action} ¥{amount} 超过 ¥{max_amount}")
                # 通过闸门(执行前记录, 执行后更新已花)
                rec = self._check(action, amount, str(kwargs.get("to", "(unknown)")))
                try:
                    result = fn(*args, **kwargs)
                except Exception as e:
                    rec.decision = "failed"
                    rec.reason = str(e)[:120]
                    self.log(rec)
                    raise
                self._spent += amount
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
        return self

    def _authorize(self, action: str, amount: float, to: str) -> bool:
        """MCP/程序化调用入口: 走全部闸门, 通过返回 True, 被拦抛异常"""
        if self.default_max_amount > 0 and amount > self.default_max_amount:
            rec = self._record(action=action, amount=amount, to=to,
                               decision="blocked_budget",
                               reason=f"单次 ¥{amount:.2f} > 上限 ¥{self.default_max_amount:.2f}",
                               spent_after=self._spent)
            self.log(rec)
            raise BudgetExceeded(f"[单次上限] {action} ¥{amount} 超过 ¥{self.default_max_amount}")
        self._check(action, amount, to)
        return True

    def summary(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "budget": self.budget,
            "spent": round(self._spent, 2),
            "remaining": round(max(self.budget - self._spent, 0), 2) if self.budget > 0 else None,
            "records": len(self.records),
            "blocked": sum(1 for r in self.records if r.decision.startswith("blocked") or r.decision == "dry_run"),
            "executed": sum(1 for r in self.records if r.decision == "executed"),
        }

    def export_audit(self, path: str = "spendguard_audit.json") -> str:
        """导出审计日志"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump([r.to_dict() for r in self.records], f, ensure_ascii=False, indent=2)
        return path
