#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SpendShield execution demo — authorization as a capability the execution layer requires.

Thesis: authorization is not advice. The execution gateway REFUSES anything that
does not carry a valid, unconsumed SpendShield grant.

  agent -> authorize() -> ALLOW  -> grant token issued
  gateway: verify + consume grant -> executes -> SUCCESS
  same token again                -> REFUSED (REUSED - one-time grant)
  no token / forged token         -> REFUSED (fail-closed)

Run:
    python examples/execution_gateway_demo.py

WARNING: mock gateway + mock rail. No real money moves.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from spendshield import SpendShield  # noqa: E402
from spendshield.enforce import AuthorizationIssuer, Executor  # noqa: E402

SECRET = "execution-demo-secret"


def gate(shield: SpendShield, agent: str, amount: float, to: str):
    """Return (grant token, policy_version), or ('', '') if not allowed."""
    r = shield.authorize(agent=agent, amount=amount, to=to,
                         meta={"intent": "execution gateway demo"})
    if r.decision != "ALLOW":
        print("  authorize -> [%s] %s (no grant issued)" % (r.decision, r.reason))
        return "", ""
    grant = issuer.issue(agent=agent, amount=amount, currency="USD", merchant=to,
                         policy_version=r.policy_version)
    print("  authorize -> [ALLOW] grant issued (policy v%s)" % r.policy_version)
    return grant, r.policy_version


def attempt(executor: Executor, label: str, token: str, pv: str, amount: float, to: str) -> str:
    ok, why = executor.verify(token, agent="mcd_bot", amount=amount, currency="USD",
                              merchant=to, policy_version=pv)
    if ok:
        print("  [gateway] %s -> EXECUTES (grant verified %s)" % (label, why))
        return "SUCCESS"
    print("  [gateway] %s -> REFUSED (%s)" % (label, why))
    return "REFUSED"


if __name__ == "__main__":
    issuer = AuthorizationIssuer(secret=SECRET)
    executor = Executor(secret=SECRET)

    print("SpendShield execution demo — no authorization, no execution")
    print("policy: daily $60 / single-tx $50 / merchant allowlist (policy.dogfood.yaml)\n")

    shield = SpendShield(policy=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             "policy.dogfood.yaml"),
                         log=lambda rec: None)
    # dogfood policy: daily 60 / max 50 / allowed mcdonalds.com + stripe.com
    print("case 1: agent legitimately buys breakfast, $25 @ mcdonalds.com")
    t1, pv1 = gate(shield, "mcd_bot", 25, "mcdonalds.com")
    print("        first call  ->", attempt(executor, "call 1 (valid grant)", t1, pv1, 25, "mcdonalds.com"))
    print("        second call ->", attempt(executor, "call 2 (same token again)", t1, pv1, 25, "mcdonalds.com"))

    print("\ncase 2: attacker tries the rail with no grant")
    print("        ", attempt(executor, "direct call, no token", "", "", 25, "mcdonalds.com"))

    print("\ncase 3: attacker forges a $500 grant with their own key")
    forged = AuthorizationIssuer(secret="attacker-key").issue(
        agent="mcd_bot", amount=500, currency="USD", merchant="scam-vip.com")
    print("        ", attempt(executor, "forged grant", forged, "2.1.0", 500, "scam-vip.com"))

    print("\ncase 4: attacker tampers with a real grant (bumps $25 -> $5000)")
    body, sig = t1.split(".")
    import base64, json as _json
    payload = _json.loads(base64.urlsafe_b64decode(body.encode() + b"=" * (-len(body) % 4)))
    payload["amount"] = 5000
    body2 = base64.urlsafe_b64encode(_json.dumps(payload, sort_keys=True).encode()).decode()
    print("        ", attempt(executor, "tampered grant", f"{body2}.{sig}", pv1, 5000, "mcdonalds.com"))

    print("\nsummary: 1 execute, 3 refusals — the gateway only moves money with a valid, "
          "unconsumed, un-forgeable grant.")
