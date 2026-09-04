#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""0/1 trusted-path run (VA issue #7 promise) — observe real behavior, don't pre-judge.

Question: with amount fixed through the host trusted path at 1,
  - model sends amount=1 (exact value)  -> how many handler invocations? ALLOW?
  - model sends amount=0 (changed)      -> how many handler invocations? blocked?
Observed result is printed; exit 0 iff the experiment itself ran cleanly.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, "/tmp/va_runpkg")  # pinned verb-authority source (VA main 5ef6e110)

from verb_authority import Param, Risk, Tool, Registry, build_policy, GuardedToolRunner  # noqa: E402

from spendshield import SpendShield  # noqa: E402

POLICY_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "policy.va_e2e.yaml")
shield = SpendShield(policy=POLICY_YAML, log=lambda rec: None)
INVOKED = {"n": 0}


def authorize_payment_handler(recipient, amount, purpose, agent_id):
    """与已验证的 composition/va_e2e_authorize_payment.py handler 同语义。"""
    INVOKED["n"] += 1
    res = shield.authorize(agent=agent_id, amount=amount, to=recipient,
                           meta={"intent": purpose})
    if res.decision != "ALLOW":
        return {"decision": res.decision, "reason": getattr(res, "reason", "")}
    return {"decision": "ALLOW"}


reg = Registry()
reg.add(Tool(
    "authorize_payment",
    [Param("recipient", "string", sink=True),
     Param("amount", "number", sink=True),
     Param("agent_id", "string", sink=True),
     Param("purpose", "string", sink=False)],
    risk=Risk.FINANCIAL,
    fn=authorize_payment_handler,
))
ps = build_policy(reg)
runner = GuardedToolRunner(reg, ps)

TRUSTED = {"recipient": "mcdonalds.com", "amount": 1, "agent_id": "mcd_bot"}  # trusted amount = 1


def attempt(label, agent_args):
    INVOKED["n"] = 0
    res = runner.run({"name": "authorize_payment", "input": agent_args},
                     trusted_args=TRUSTED,
                     confirm=lambda _req: True)
    print(f"[{label}]")
    print(f"    handler invocations = {INVOKED['n']}   (independent counter)")
    print(f"    runner.invoked      = {bool(res.invoked)}")
    print(f"    allow               = {res.decision.allow}")
    if INVOKED["n"] == 1:
        r = res.result or {}
        print(f"    handler decision    = {r.get('decision')}")
    return INVOKED["n"]


import spendshield as _ss
print("0/1 trusted-path run — trusted amount fixed at 1 (host side, outside model envelope)")
print(f"spendshield version : {getattr(_ss, '__version__', 'unknown')}")
print(f"verb-authority pin  : 5ef6e1109120 (module source: /tmp/va_runpkg, same env as the 7-case harness)")
print("=" * 68)
n_exact = attempt("model sends amount=1 (exact trusted value)",
                  {**TRUSTED, "amount": 1, "purpose": "order breakfast"})
print()
n_zero = attempt("model sends amount=0 (changed from trusted 1)",
                 {**TRUSTED, "amount": 0, "purpose": "order breakfast for $0"})
print()
print(f"observed: exact-value attempt -> {n_exact} invocation(s); "
      f"changed-value attempt -> {n_zero} invocation(s)")
sys.exit(0)
