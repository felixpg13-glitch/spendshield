# -*- coding: utf-8 -*-
"""
x402 durable-intent client (Phase C, issue #2) — 让 x402 客户端支付流程走
durable exactly-once 边界。

设计文档: docs/intent-x402-integration.md
  - 三态 rail lookup: COMMITTED / NOT_EXECUTED_PROVEN / INDETERMINATE (fail-closed)
  - binding: rail result 必须绑定 intent.provider_key, intent_id 不是 trust boundary
  - guard.book() = cache projection; ledger row 才是 correctness boundary

与旧 spendshield/adapters/x402.py 的关系 (Phase C 纪律):
  - 本模块是全新 durable intent path, 不改旧 protect_x402_payment() 语义;
    旧 API 迁移等本路径稳定后再决定。

用法:
    guard = SpendShield(...)
    store = SqliteIntentStore("/path/intents.db")
    client = X402IntentClient(guard, store)

    out = client.protect(agent, to, amount, idem_key, fingerprint)
    # out["status"] == "IN_FLIGHT" → caller 拿 out["provider_key"] 执行 rail
    # out["status"] != "IN_FLIGHT" → 已有 in-flight/ambiguous/已定论 intent → 先 recover

    rail 结果回来后:
    client.complete(out["intent_id"], {"provider_key": out["provider_key"],
                                        "outcome": "committed",
                                        "proof": "<rail txn id>"})
    # committed → durable booking + cache; 返回 True ⇔ 本次执行了 booking

    启动/重试前:
    report = client.recover(rail_lookup)   # rail_lookup.lookup(provider_key) -> 三态
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..guard import SpendShield
from ..intent import (IN_FLIGHT, RECONCILED_FAILED, RESERVED, SUCCEEDED,
                      UNKNOWN, InvalidTransition, SqliteIntentStore)

_COMMITTED = "COMMITTED"
_NOT_EXECUTED_PROVEN = "NOT_EXECUTED_PROVEN"
_INDETERMINATE = "INDETERMINATE"  # noqa: F841  (文档化; recover 对非 COMMITTED/PROVEN 一律 fail-closed)


class X402IntentClient:
    """薄适配层: rail 编排留在 caller; 本类只负责 durable intent 机制。"""

    def __init__(self, guard: SpendShield, store: SqliteIntentStore):
        self.guard = guard
        self.store = store

    # ── protect: 授权 → durable reservation → claim dispatch ──────────
    def protect(self, agent: str, to: str, amount: float, idem_key: str,
                fingerprint: dict, action: str = "x402 支付",
                currency: str = "USD") -> dict:
        """授权 + 建立 durable intent + 赢下 dispatch claim。

        返回 {intent_id, provider_key, status}:
          - status == "IN_FLIGHT" → caller 可执行 rail (provider_key 幂等键)
          - status != "IN_FLIGHT" → 已有 in-flight/ambiguous/已定论 intent,
            caller 必须 recover(), 绝不 dispatch (UNKNOWN ≠ retry allowed)
        """
        # 1) policy gate (抛 GuardedError 子类 → caller 不得 dispatch)
        self.guard._authorize(action, amount, to, agent=agent)
        # 2) durable reservation (幂等 create-or-get)
        intent = self.store.create_or_get(agent=agent, to=to, idem_key=idem_key,
                                          amount=amount, fingerprint=fingerprint,
                                          currency=currency)
        if intent["status"] != RESERVED:
            return self._pick(intent)  # recovery branch — 不 dispatch
        # 3) pre-dispatch eligibility + 4) CAS claim (唯一 sender)
        if not self.store.reserve(intent["id"]):
            return self._pick(self.store.get(intent["id"]))
        if not self.store.mark_in_flight(intent["id"]):
            return self._pick(self.store.get(intent["id"]))
        return self._pick(self.store.get(intent["id"]))

    # ── complete: caller 回报 rail 结果 ────────────────────────────────
    def complete(self, intent_id: int, rail_result: dict) -> bool:
        """按 rail 结果推进 durable state。

        rail_result = {provider_key, outcome, proof}
          outcome ∈ committed | failed | ambiguous
        committed/failed 必须通过 binding 校验 (rail_result.provider_key ==
        intent.provider_key); 不匹配/缺失 → raise, intent 不变。

        返回 True ⇔ 本次调用执行了 durable booking (consume_once 赢家);
        failed/ambiguous/no-op 返回 False。
        """
        intent = self.store.get(intent_id)
        if intent is None:
            raise InvalidTransition(f"unknown intent {intent_id}")
        outcome = rail_result.get("outcome")
        if outcome not in ("committed", "failed", "ambiguous"):
            raise InvalidTransition(f"invalid rail outcome {outcome!r}")

        if outcome in ("committed", "failed"):
            # binding 校验: 外部 result 必须属于这把 provider_key
            rk = rail_result.get("provider_key")
            if not rk or rk != intent["provider_key"]:
                raise InvalidTransition(
                    f"unbound rail result: provider_key {rk!r} does not match "
                    f"intent {intent_id} provider_key {intent['provider_key']!r} "
                    f"— intent_id is not the trust boundary")
            proof = rail_result.get("proof") or f"rail:{outcome}:{rk}"
        else:
            proof = ""

        st = intent["status"]
        if outcome == "committed":
            if st == RESERVED:
                raise InvalidTransition("intent never dispatched; cannot be committed")
            if st == RECONCILED_FAILED:
                raise InvalidTransition(
                    f"intent proven failed but rail reports committed — contradiction")
            if st == UNKNOWN:
                self.store.reconcile(intent_id, SUCCEEDED, proof)   # 唯一推进 UNKNOWN 的入口
            elif st == IN_FLIGHT:
                self.store.mark_succeeded(intent_id)
            # st == SUCCEEDED → 直接走 settle (幂等)
            if self.store.consume_once(intent_id):
                # durable booking 已 commit → guard.book = cache projection
                self.guard.book(agent=intent["agent"], amount=intent["amount"],
                                to=intent["to"])
                return True
            return False
        if outcome == "failed":
            if st in (SUCCEEDED, RECONCILED_FAILED):
                return False  # 已定论 no-op
            if st == RESERVED:
                raise InvalidTransition("intent never dispatched; use cancel_reserved")
            return self.store.reconcile(intent_id, RECONCILED_FAILED, proof)
        # ambiguous → UNKNOWN (无外部证据, 无 binding 可言)
        if st == RESERVED:
            raise InvalidTransition("intent never dispatched; cannot be ambiguous")
        if st == IN_FLIGHT:
            self.store.mark_unknown(intent_id)
        return False

    # ── recover: 启动/重试前收敛 (歧义 pass → booking pass) ───────────
    def recover(self, rail_lookup: Callable[[str], Any]) -> dict:
        """重启收敛。rail_lookup.lookup(provider_key) 必须返回三态之一:
        "COMMITTED" / "NOT_EXECUTED_PROVEN" / "INDETERMINATE"
        (或 (state, proof) 元组)。INDETERMINATE 与任何未知 → fail-closed。

        ① 歧义 pass: IN_FLIGHT/UNKNOWN 由 rail 证据裁决 (绝不本地假设)
        ② booking pass: SUCCEEDED → consume_once (ledger) → guard.book (cache)
        返回 {"booked": n, "failed": m, "indeterminate": k}
        """
        booked = failed = indeterminate = 0
        lookup_fn = rail_lookup if callable(rail_lookup) else rail_lookup.lookup
        # ① 歧义 pass
        for it in self.store.list_by_status(IN_FLIGHT, UNKNOWN):
            pk = it["provider_key"]
            try:
                state = lookup_fn(pk)
            except Exception:
                indeterminate += 1
                continue
            proof = ""
            if isinstance(state, tuple):  # (state, proof)
                state, proof = state
            if state == _COMMITTED:
                self.store.reconcile(it["id"], SUCCEEDED,
                                     proof or f"rail:committed:{pk}")
            elif state == _NOT_EXECUTED_PROVEN:
                if self.store.reconcile(it["id"], RECONCILED_FAILED,
                                        proof or f"rail:absent:{pk}"):
                    failed += 1
            else:
                indeterminate += 1  # INDETERMINATE / 未知 → fail-closed
        # ② booking pass: durable → cache
        for it in self.store.list_by_status(SUCCEEDED):
            if self.store.consume_once(it["id"]):
                self.guard.book(agent=it["agent"], amount=it["amount"], to=it["to"])
                booked += 1
        return {"booked": booked, "failed": failed, "indeterminate": indeterminate}

    # ── 内部 ──────────────────────────────────────────────────────────
    @staticmethod
    def _pick(intent: Optional[dict]) -> dict:
        if intent is None:
            raise InvalidTransition("intent vanished")
        return {"intent_id": intent["id"],
                "provider_key": intent["provider_key"],
                "status": intent["status"]}
