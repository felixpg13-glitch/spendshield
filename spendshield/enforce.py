# -*- coding: utf-8 -*-
"""
Enforcement Prototype (2026-09-02) — 最小闭环: Signed Authorization + Executor Verify

advisory 层(guard.authorize)回答 "should it happen?"
enforcement 层回答: "没有有效授权凭证, 钱动不了"。

设计(原型, 非生产):
  - 签发: 用 HMAC-SHA256 对规范化 intent 签名 → token
  - 验证: Executor 在付款前 verify(token, intent)
    * 签名不符 / 篡改 amount/merchant/currency/policy_version / 过期 / 重放 / 伪造 → FAIL(结构化原因)
  - 一次性: Executor 记录已用 token, 重复使用 → FAIL
  - fail-closed: 任何解析/签名错误默认拒绝

原型刻意不碰 guard.py 的现有 authorize 流; 生产化方向见 docs/SECURITY_HARDENING_BACKLOG.md P0-1。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from typing import Optional

_FIELDS = ("agent", "amount", "currency", "merchant", "policy_version")


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class AuthorizationIssuer:
    """签发一次性授权凭证(HMAC-SHA256)。原型: 与 Executor 共享 secret; 生产应分进程/分凭证。"""

    def __init__(self, secret: Optional[str] = None, default_ttl: int = 300):
        self.secret = (secret or os.environ.get("SPENDSHIELD_AUTHZ_SECRET") or "dev-secret-change-me").encode()
        self.default_ttl = default_ttl

    def issue(self, *, agent: str, amount: float, currency: str, merchant: str,
              policy_version: str = "", ttl: Optional[int] = None, sources: Optional[dict] = None) -> str:
        payload = {
            "agent": agent, "amount": amount, "currency": currency, "merchant": merchant,
            "policy_version": policy_version,
            "nonce": uuid.uuid4().hex, "iat": int(time.time()),
            "exp": int(time.time()) + (ttl or self.default_ttl),
        }
        # provenance/source binding(可选): 声明每个字段的来源(trusted_args / agent / tool_output ...)
        # 签名覆盖 source 标签 → 来源标签和值一样不可篡改。注意: 只能绑定“声明的来源”,
        # 不能验证“来源标签是否诚实”——那是上游 source-aware PEP/scanner(如 Verb Authority)的层。
        if sources:
            for f, src in sources.items():
                if f in _FIELDS:
                    payload[f"{f}_source"] = str(src)
        body = base64.urlsafe_b64encode(_canonical(payload)).decode()
        sig = hmac.new(self.secret, _canonical(payload), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"


class Executor:
    """付款执行方: 没有有效授权凭证, 绝不执行。fail-closed。"""

    def __init__(self, secret: Optional[str] = None):
        self.secret = (secret or os.environ.get("SPENDSHIELD_AUTHZ_SECRET") or "dev-secret-change-me").encode()
        self._used: set = set()
        self._lock = threading.Lock()  # 检查-记账原子性: 并发双花防护(同进程内)

    def verify(self, token: str, *, agent: str, amount: float, currency: str,
               merchant: str, policy_version: str = "", sources: Optional[dict] = None) -> tuple[bool, str]:
        """校验 token 是否对该 intent 有效。返回 (ok, reason)。失败原因结构化可编程消费。
        sources: 执行时声明各字段来源(与签发时的来源绑定比对, 缺失/不符 → 拒绝)。"""
        try:
            body, sig = token.split(".", 1)
            payload = json.loads(base64.urlsafe_b64decode(body.encode() + b"=" * (-len(body) % 4)))
        except Exception:
            return False, "MALFORMED_TOKEN"

        # 1. 签名必须匹配(伪造/篡改都在这挂)
        expect = hmac.new(self.secret, _canonical(payload), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, sig):
            return False, "INVALID_SIGNATURE"

        # 2. 字段绑定: 授权的是这个 intent, 不是别的
        for f in _FIELDS:
            tok_v, exp_v = payload.get(f), {"amount": amount, "agent": agent, "currency": currency,
                                            "merchant": merchant, "policy_version": policy_version}[f]
            if str(tok_v) != str(exp_v):
                return False, f"FIELD_MISMATCH:{f}"

        # 2b. 来源绑定(authorship-channel): grant 声明过来源的字段, 执行时必须一致, 否则 fail closed
        sources = sources or {}
        for f in _FIELDS:
            skey = f"{f}_source"
            if skey in payload:                      # grant 绑定了来源
                if f not in sources:
                    return False, f"SOURCE_MISSING:{f}"      # 执行时没声明来源
                if str(sources[f]) != str(payload[skey]):
                    return False, f"SOURCE_MISMATCH:{f}"     # 来源被替换(trusted→agent 等)
        # 2c. 反向 fail-closed(2026-09-03 VA review): 调用方声明了 sources 时,
        #      token 必须也声明了对应标签 — 防“签发时漏掉全部 source 标签仍 AUTHORIZED”
        if sources:
            for f in sources:
                if f in _FIELDS and f"{f}_source" not in payload:
                    return False, f"SOURCE_MISSING:{f}"

        # 3. 过期
        if payload.get("exp", 0) < time.time():
            return False, "EXPIRED"

        # 4. 一次性(重放防护; 原型内存态+进程内锁, 跨进程持久化属生产 TODO)
        rid = hashlib.sha256(_canonical(payload)).hexdigest()
        with self._lock:
            if rid in self._used:
                return False, "REUSED"
            self._used.add(rid)

        return True, "AUTHORIZED"
