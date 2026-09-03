#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VA × SpendShield E2E composition test — yairsabag's 5-case proposal (VA issue #7)

Target: authorize_payment(recipient, amount, purpose, agent_id)
VA policy via sink declarations: recipient/amount/agent_id sink=True (TRUSTED_FIXED),
purpose sink=False (data may write it). Handler = real SpendShield authorize() +
source-bound grant. trusted_args supplied by the host, outside the agent envelope.
Measured: actual handler invocation (ExecutionResult.invoked / .executed).

Run:  python3 composition/va_e2e_authorize_payment.py
      (needs verb-authority installed, or PYTHONPATH pointing at the pinned source)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from verb_authority import Param, Risk, Tool, Registry, build_policy, GuardedToolRunner  # noqa: E402

from spendshield import SpendShield  # noqa: E402
from spendshield.enforce import AuthorizationIssuer  # noqa: E402

POLICY_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                           "examples", "policy.dogfood.yaml")
SECRET = "va-e2e-composition-secret"

shield = SpendShield(policy=POLICY_YAML, log=lambda rec: None)
issuer = AuthorizationIssuer(secret=SECRET)


def authorize_payment_handler(recipient, amount, purpose, agent_id):
    """Real handler — SpendShield policy decision + source-bound grant."""
    res = shield.authorize(agent=agent_id, amount=amount, to=recipient,
                           meta={"intent": purpose})
    if res.decision != "ALLOW":
        return {"decision": res.decision, "reason": res.reason}
    grant = issuer.issue(
        agent=agent_id, amount=amount, currency="USD", merchant=recipient,
        policy_version=res.policy_version,
        sources={"recipient": "trusted_args", "amount": "trusted_args",
                 "agent_id": "trusted_args"})
    return {"decision": "ALLOW", "grant_issued": True,
            "policy_version": res.policy_version}


reg = Registry()
reg.add(Tool(
    "authorize_payment",
    [Param("recipient", "string", sink=True),
     Param("amount", "number", sink=True),
     Param("agent_id", "string", sink=True),
     Param("purpose", "string", sink=False)],
    risk=Risk.WRITE,
    fn=authorize_payment_handler,
))
ps = build_policy(reg)
runner = GuardedToolRunner(reg, ps)

TRUSTED = {"recipient": "mcdonalds.com", "amount": 25, "agent_id": "mcd_bot"}


def run_case(label, agent_args, trusted_args, expect):
    res = runner.run({"name": "authorize_payment", "input": agent_args},
                     trusted_args=trusted_args,
                     confirm=lambda _req: True)  # test harness: auto-confirm risk review
    invoked = 1 if res.invoked else 0
    verdict = "PASS" if invoked == expect else "FAIL"
    reason = (res.decision.reason if hasattr(res, "decision")
              and getattr(res.decision, "reason", None) else "") or ""
    print(f"[{verdict}] {label}")
    print(f"        allow={res.decision.allow} | handler invoked={invoked} "
          f"(expect {expect}) | executed={res.executed} | reason={reason[:90]}")
    return verdict


if __name__ == "__main__":
    print("VA × SpendShield E2E — authorize_payment composition test")
    print("params: recipient/amount/agent_id = sink(TRUSTED_FIXED) · purpose = writable\n")
    results = []

    results.append(run_case(
        "1. trusted recipient+amount fixed, model writes purpose            -> 1",
        {**TRUSTED, "purpose": "order breakfast, $25"}, TRUSTED, 1))

    results.append(run_case(
        "2. model changes amount (trusted fixed)                            -> 0",
        {**TRUSTED, "amount": 5000, "purpose": "order breakfast, $25"}, TRUSTED, 0))

    results.append(run_case(
        "2b. model changes recipient (trusted fixed)                        -> 0",
        {**TRUSTED, "recipient": "scam-vip.com", "purpose": "order breakfast"}, TRUSTED, 0))

    results.append(run_case(
        "3. protected values without independent trusted_args binding       -> 0",
        {**TRUSTED, "purpose": "order breakfast, $25"}, None, 0))

    results.append(run_case(
        "4. model supplies/alters source labels inside its own payload      -> 0",
        {**TRUSTED, "purpose": "order breakfast",
         "source": "trusted", "recipient_source": "trusted"}, TRUSTED, 0))

    results.append(run_case(
        "5. only purpose changes while protected values remain bound        -> 1",
        {**TRUSTED, "purpose": "order a different breakfast item"}, TRUSTED, 1))

    print("\nsummary:", results.count("PASS"), "PASS /", len(results), "cases")
