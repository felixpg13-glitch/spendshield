# -*- coding: utf-8 -*-
"""
SpendShield 0.8 — Policy Lifecycle(策略生命周期治理)

解决「谁有权定义判断 / 怎么验证 / 怎么上线 / 怎么撤回」:

  CREATE → VALIDATE → SIMULATE → SCAN → REVIEW → APPLY → VERSION → ROLLBACK → AUDIT

核心原则(Felix 定):
  - Apply 是危险边界: 不允许跳过任何验证阶段直接上线
  - 每个生命周期事件写入审计哈希链(谁做的 / 什么时候 / 为什么)
  - 版本化 + 可回滚: 任何时刻能回答「线上跑的是哪个版本, 为什么」
"""
from __future__ import annotations

import dataclasses
import json
import time
import uuid
from typing import Any, Optional


class PolicyLifecycleError(Exception):
    """生命周期状态机违规"""


# 状态机: 只能按顺序前进, 不允许跳跃
VALID_TRANSITIONS = {
    "CREATED": ("VALIDATED",),
    "VALIDATED": ("SIMULATED",),
    "SIMULATED": ("SCANNED",),
    "SCANNED": ("REVIEWED",),
    "REVIEWED": ("APPLIED",),
    "APPLIED": (),          # 终态(新版本用新 draft)
}


@dataclasses.dataclass
class PolicyDraft:
    id: str
    name: str
    policy: dict
    state: str = "CREATED"
    created_by: str = ""
    created_at: float = 0.0
    history: list = dataclasses.field(default_factory=list)   # [(state, by, ts)]

    def transition(self, to_state: str, by: str) -> None:
        if to_state not in VALID_TRANSITIONS.get(self.state, ()):
            raise PolicyLifecycleError(
                f"非法状态跳转: {self.state} → {to_state}(只允许 {'/'.join(VALID_TRANSITIONS.get(self.state, ()))})")
        self.state = to_state
        self.history.append((to_state, by, time.time()))


class PolicyManager:
    """策略生命周期管理: 验证/模拟/扫描/评审/上线/回滚, 全部审计留痕"""

    def __init__(self, guard):
        self.guard = guard
        self._lock = __import__("threading").RLock()   # apply/rollback 原子性(并发只有一个赢家)
        self.drafts: dict[str, PolicyDraft] = {}
        self._versions: dict[str, dict] = {}     # version -> policy dict
        self._applied_by: dict[str, str] = {}    # version -> 操作者
        # 登记当前生产策略为初始版本(回滚目标)
        if getattr(guard, "_v2_policy", None):
            p = guard._v2_policy
            raw = {"version": p.version, "policy": dataclasses.asdict(p)}
            self._versions[p.version] = raw
            self._applied_by[p.version] = "(initial)"

    # ── CREATE ────────────────────────────────────────────
    def create(self, name: str, policy: dict, by: str = "") -> PolicyDraft:
        if not isinstance(policy, dict) or "policy" not in policy:
            raise PolicyLifecycleError("policy 必须是含 policy 段的 dict")
        d = PolicyDraft(id=uuid.uuid4().hex[:12], name=name, policy=dict(policy),
                        created_by=by, created_at=time.time())
        d.history.append(("CREATED", by, time.time()))
        self.drafts[d.id] = d
        self._audit("policy_create", d, by, f"created draft {d.id} ({name})")
        return d

    # ── VALIDATE ──────────────────────────────────────────
    def validate(self, draft_id: str, by: str = "") -> dict:
        d = self._get(draft_id)
        d.transition("VALIDATED", by)
        from .validator import validate_raw
        errs = validate_raw(d.policy)
        self._audit("policy_validate", d, by, f"validate: {'OK' if not errs else errs}")
        return {"ok": not errs, "errors": errs, "draft_id": draft_id}

    # ── SIMULATE ──────────────────────────────────────────
    def simulate(self, draft_id: str, cases: Optional[list] = None, by: str = "") -> dict:
        d = self._get(draft_id)
        d.transition("SIMULATED", by)
        from .simulator import PolicySimulator
        sim = PolicySimulator(policy_raw=d.policy)
        cases = cases or [{"agent": "", "amount": 1, "to": "example.com"},
                          {"agent": "", "amount": 100, "to": "example.com"},
                          {"agent": "", "amount": 1000, "to": "example.com"}]
        results = []
        for c in cases:
            r = sim.evaluate(c.get("agent", ""), float(c.get("amount", 0)), c.get("to", "?"))
            results.append({"case": c, "decision": r.decision, "reason": r.reason})
        self._audit("policy_simulate", d, by, f"simulated {len(cases)} cases")
        return {"ok": True, "results": results, "draft_id": draft_id}

    # ── SCAN(安全扫描: 规则冲突/危险配置/变更摘要) ────────
    def scan(self, draft_id: str, by: str = "") -> dict:
        d = self._get(draft_id)
        findings = self._scan_findings(d.policy)
        blockers = [f for f in findings if f["severity"] == "blocker"]
        if not blockers:
            d.transition("SCANNED", by)   # 有 blocker 不前进 → review/apply 被状态机自然阻断
        self._audit("policy_scan", d, by, f"scan: {len(findings)} findings, {len(blockers)} blockers")
        return {"ok": not blockers, "findings": findings, "draft_id": draft_id}

    @staticmethod
    def _scan_findings(policy: dict) -> list[dict]:
        findings = []
        p = policy.get("policy", policy)
        m = p.get("merchants", {}) or {}
        allowed = set(str(x).lower() for x in (m.get("allowed") or []))
        blocked = set(str(x).lower() for x in (m.get("blocked") or []))
        overlap = allowed & blocked
        if overlap:
            findings.append({"severity": "warn", "code": "RULE_CONFLICT",
                             "message": f"商户同时出现在白名单和黑名单(黑名单优先): {sorted(overlap)}"})
        b = p.get("budget", {}) or {}
        t = p.get("transaction", {}) or {}
        a = p.get("approval", {}) or {}
        no_limits = (not b.get("daily") and not b.get("monthly") and not b.get("total")
                     and not t.get("max") and not a.get("over") and not a.get("new_merchant"))
        if no_limits and not allowed and not blocked:
            findings.append({"severity": "blocker", "code": "UNLIMITED_SPEND",
                             "message": "策略无任何限制(无预算/无限额/无审批/无名单)= 完全放行, 高危"})
        elif no_limits:
            findings.append({"severity": "warn", "code": "NO_LIMITS",
                             "message": "无预算/单笔上限/审批阈值(仅靠名单约束)"})
        if not b.get("daily") and not b.get("monthly") and not b.get("total"):
            findings.append({"severity": "info", "code": "NO_BUDGET",
                             "message": "未配置预算(建议设 daily/monthly 上限)"})
        return findings

    # ── REVIEW(人审) ──────────────────────────────────────
    def review(self, draft_id: str, by: str = "", approve: bool = True) -> dict:
        d = self._get(draft_id)
        if d.state != "SCANNED":
            raise PolicyLifecycleError(f"REVIEW 前必须 SCANNED(当前 {d.state})")
        if not by:
            raise PolicyLifecycleError("review 必须指定审批人(by)")
        if not approve:
            self._audit("policy_review_rejected", d, by, f"rejected by {by}")
            return {"ok": False, "reason": f"rejected by {by}", "draft_id": draft_id}
        d.transition("REVIEWED", by)
        self._audit("policy_review", d, by, f"approved by {by}")
        return {"ok": True, "draft_id": draft_id}

    # ── APPLY(危险边界: 必须 REVIEWED) ────────────────────
    def apply(self, draft_id: str, by: str = "") -> dict:
        d = self._get(draft_id)
        if d.state != "REVIEWED":
            raise PolicyLifecycleError(f"APPLY 前必须 REVIEWED(当前 {d.state}): 不允许跳过评审上线")
        version = str(d.policy.get("version", f"v{int(time.time())}"))
        with self._lock:
            # 版本冲突检测: 同 version 不同内容 → 拒绝(历史不可覆盖)
            if version in self._versions:
                import json as _j
                old_p = self._versions[version].get("policy", {})
                new_p = d.policy.get("policy", {})
                if _j.dumps(old_p, sort_keys=True, default=str) != _j.dumps(new_p, sort_keys=True, default=str):
                    raise PolicyLifecycleError(
                        f"version '{version}' 已存在且内容不同: 不允许覆盖历史(请用新版本号)")
            old_version = self.guard._v2_policy.version if self.guard._v2_policy else "(none)"
            self.guard._setup_v2(dict(d.policy))
            self._versions[version] = dict(d.policy)
            self._applied_by[version] = by
            d.transition("APPLIED", by)
            self._audit("policy_apply", d, by, f"applied {old_version} -> {version} by {by}")
        return {"ok": True, "version": version, "from": old_version, "draft_id": draft_id}

    # ── VERSION / ROLLBACK ────────────────────────────────
    def versions(self) -> list[dict]:
        return [{"version": v, "applied_by": self._applied_by.get(v, ""),
                 "policy": p} for v, p in self._versions.items()]

    def rollback(self, version: str, by: str = "") -> dict:
        with self._lock:
            if version not in self._versions:
                raise PolicyLifecycleError(f"版本不存在: {version}")
            current = self.guard._v2_policy.version if self.guard._v2_policy else "(none)"
            self.guard._setup_v2(dict(self._versions[version]))
            self._audit("policy_rollback", None, by, f"rolled back {current} -> {version} by {by}")
        return {"ok": True, "version": version, "from": current}

    def diff(self, v1: str, v2: str) -> str:
        from .versioning import diff as vdiff
        a = self._versions.get(v1) or {"policy": self.guard._v2_policy and dataclasses.asdict(self.guard._v2_policy)}
        b = self._versions.get(v2)
        if a is None or b is None:
            return f"(missing version: {v1 if a is None else v2})"
        import json as _j
        sa = _j.dumps(a.get("policy", a), ensure_ascii=False, indent=2, sort_keys=True).splitlines()
        sb = _j.dumps(b.get("policy", b), ensure_ascii=False, indent=2, sort_keys=True).splitlines()
        import difflib
        return "\n".join(difflib.unified_diff(sa, sb, fromfile=v1, tofile=v2, lineterm=""))

    # ── 内部 ──────────────────────────────────────────────
    def _get(self, draft_id: str) -> PolicyDraft:
        if draft_id not in self.drafts:
            raise PolicyLifecycleError(f"draft 不存在: {draft_id}")
        return self.drafts[draft_id]

    def _audit(self, action: str, draft: Optional[PolicyDraft], by: str, reason: str) -> None:
        self.guard.audit.append(
            request_id="", actor=by or "?", action=action, decision="EXECUTED",
            reason_codes=[], primary_reason=reason,
            amount=0.0, currency="", merchant="", approval_state="none",
            input_hash="", meta={"draft": draft.id if draft else "", "policy": draft.name if draft else ""},
            policy_version=draft.policy.get("version", "?") if draft else "",
            policy_hash=self.guard._policy_fp(),
            engine_version=__import__("spendshield", fromlist=["__version__"]).__version__)
