# -*- coding: utf-8 -*-
"""
SpendShield V2 Policy Engine — 校验器(快速失败: 坏 policy 拒绝加载)
"""
from __future__ import annotations

from typing import Optional

from .schema import Policy


class PolicyValidationError(ValueError):
    pass


def validate_raw(raw: dict) -> Optional[list[str]]:
    """返回错误列表; 空列表 = 通过。不抛异常, 便于收集所有错误。"""
    errs: list[str] = []
    if not isinstance(raw, dict):
        return ["policy must be a YAML/JSON object"]
    if not raw.get("version"):
        errs.append("missing required field: version")
    p = raw.get("policy", raw)

    # budget
    b = p.get("budget", {}) or {}
    for k in ("daily", "monthly", "total"):
        if k in b and b[k] is not None:
            try:
                if float(b[k]) < 0:
                    errs.append(f"budget.{k} must be >= 0")
            except (TypeError, ValueError):
                errs.append(f"budget.{k} must be a number")

    # transaction
    t = p.get("transaction", {}) or {}
    for k in ("max", "min"):
        if k in t and t[k] is not None:
            try:
                if float(t[k]) < 0:
                    errs.append(f"transaction.{k} must be >= 0")
            except (TypeError, ValueError):
                errs.append(f"transaction.{k} must be a number")
    if t.get("max") is not None and t.get("min") is not None:
        try:
            if float(t["max"]) < float(t["min"]):
                errs.append("transaction.max must be >= transaction.min")
        except (TypeError, ValueError):
            pass

    # merchants
    m = p.get("merchants", {}) or {}
    for k in ("allowed", "blocked"):
        if k in m and m[k] is not None and not isinstance(m[k], list):
            errs.append(f"merchants.{k} must be a list")

    # approval
    a = p.get("approval", {}) or {}
    if a.get("channel") not in (None, "", "console", "tg", "webhook", "callable"):
        errs.append(f"approval.channel must be one of console/tg/webhook/callable, got: {a.get('channel')}")

    # rate_limit
    r = p.get("rate_limit", {}) or {}
    if "window_s" in r and r["window_s"] is not None:
        try:
            if int(r["window_s"]) <= 0:
                errs.append("rate_limit.window_s must be > 0")
        except (TypeError, ValueError):
            errs.append("rate_limit.window_s must be an int")

    # agents 段
    agents = raw.get("agents", {})
    if agents is not None and not isinstance(agents, dict):
        errs.append("agents must be a dict of agent_id -> policy")
    return errs


def load_policy(raw: dict) -> Policy:
    """校验 + 构建 Policy; 失败抛 PolicyValidationError"""
    errs = validate_raw(raw)
    if errs:
        raise PolicyValidationError("; ".join(errs))
    return Policy.from_dict(raw)
