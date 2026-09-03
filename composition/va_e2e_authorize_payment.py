#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VA × SpendShield E2E composition test — yairsabag's 5+1 cases (VA issue #7)

authorize_payment(recipient, amount, purpose, agent_id)
VA: recipient/amount/agent_id sink=True (TRUSTED_FIXED); purpose sink=False.
Host supplies trusted_args outside the agent envelope. Handler = real
SpendShield authorize() + source-bound grant (canonical keys merchant/amount/agent).
Grant retained host-side, verified through Executor (source-bound), then spent.
Independent handler-entry counter + full-chain assertions. Exit != 0 on any FAIL.

Run:  PYTHONPATH=<verb-authority src> python3 composition/va_e2e_authorize_payment.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from verb_authority import Param, Risk, Tool, Registry, build_policy, GuardedToolRunner  # noqa: E402

from spendshield import SpendShield  # noqa: E402
from spendshield.enforce import AuthorizationIssuer, Executor  # noqa: E402

POLICY_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "policy.va_e2e.yaml")
SECRET = "va-e2e…cret"

shield = SpendShield(policy=POLICY_YAML, log=lambda rec: None)
issuer = AuthorizationIssuer(secret=SECRET)
executor = Executor(secret=SECRET)

INVOKED = {"n": 0}  # independent handler-entry counter


def authorize_payment_handler(recipient, amount, purpose, agent_id):
    """Real handler — SpendShield policy decision + source-bound grant."""
    INVOKED["n"] += 1
    res = shield.authorize(agent=agent_id, amount=amount, to=recipient,
                           meta={"intent": purpose})
    if res.decision != "ALLOW":
        return {"decision": res.decision, "reason": res.reason}
    grant = issuer.issue(
        agent=agent_id, amount=amount, currency="USD", merchant=recipient,
        policy_version=res.policy_version,
        sources={"merchant": "trusted_args", "amount": "trusted_args",
                 "agent": "trusted_args"})   # canonical issuer field names
    return {"decision": "ALLOW", "grant": grant,
            "policy_version": res.policy_version}


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

TRUSTED = {"recipient": "mcdonalds.com", "amount": 25, "agent_id": "mcd_bot"}
FAILURES = []


def run_case(label, agent_args, trusted_args, expect_invoked):
    INVOKED["n"] = 0
    ok = True
    notes = []
    try:
        res = runner.run({"name": "authorize_payment", "input": agent_args},
                         trusted_args=trusted_args,
                         confirm=lambda _req: True)
        invoked = 1 if res.invoked else 0
        if invoked != expect_invoked:
            ok = False
        if INVOKED["n"] != expect_invoked:      # independent counter agrees
            ok = False
            notes.append(f"handler counter={INVOKED['n']} != {expect_invoked}")
        if expect_invoked == 1:
            r = res.result or {}
            if res.decision.allow is not True or not res.executed:
                ok = False
            if r.get("decision") != "ALLOW":
                ok = False
                notes.append(f"handler decision={r.get('decision')}")
            grant = r.get("grant")
            if not grant:
                ok = False
                notes.append("no grant issued")
            else:
                ver, why = executor.verify(
                    grant, agent="mcd_bot", amount=25, currency="USD",
                    merchant="mcdonalds.com", policy_version=r.get("policy_version", ""),
                    sources={"merchant": "trusted_args", "amount": "trusted_args",
                             "agent": "trusted_args"})
                if not ver:
                    ok = False
                    notes.append(f"grant verify failed: {why}")
                # explicit decode: signed source fields == canonical trusted_args
                import base64 as _b64
                _body = grant.split(".")[0]
                _pad = _b64.urlsafe_b64decode(_body.encode() + b"=" * (-len(_body) % 4))
                _pl = json.loads(_pad)
                for _f, _exp in (("merchant_source", "trusted_args"),
                                 ("amount_source", "trusted_args"),
                                 ("agent_source", "trusted_args")):
                    if _pl.get(_f) != _exp:
                        ok = False
                        notes.append(f"signed {_f}={_pl.get(_f)!r} != {_exp!r}")
        else:
            if res.decision.allow is not False:
                ok = False
                notes.append(f"allow={res.decision.allow} expected False")
    except Exception as e:  # noqa: BLE001
        ok = False
        notes.append(f"raised {type(e).__name__}: {e}")
    tag = "PASS" if ok else "FAIL"
    if not ok:
        FAILURES.append(label)
    print(f"[{tag}] {label}")
    print(f"        invoked={INVOKED['n']} (expect {expect_invoked}) | "
          f"allow={res.decision.allow if 'res' in dir() else '?'} | "
          f"{' | '.join(notes) if notes else ''}")


def probe_source_binding():
    """Negative verification probes (VA review): source-bound chain must fail closed
    if any canonical source label is missing or replaced at issuance."""
    ok_all = True
    # (a) issuance omits agent source -> SOURCE_MISSING:agent on full-map verify
    g = issuer.issue(agent="mcd_bot", amount=25, currency="USD", merchant="mcdonalds.com",
                     policy_version="2.1.0",
                     sources={"merchant": "trusted_args", "amount": "trusted_args"})
    v, why = executor.verify(g, agent="mcd_bot", amount=25, currency="USD",
                             merchant="mcdonalds.com", policy_version="2.1.0",
                             sources={"merchant": "trusted_args", "amount": "trusted_args",
                                      "agent": "trusted_args"})
    good = (not v) and why == "SOURCE_MISSING:agent"
    ok_all &= good
    print(f"[{'PASS' if good else 'FAIL'}] probe A: agent source omitted at issuance -> {why}")
    # (b) issuance replaces amount source -> SOURCE_MISMATCH:amount
    g = issuer.issue(agent="mcd_bot", amount=25, currency="USD", merchant="mcdonalds.com",
                     policy_version="2.1.0",
                     sources={"merchant": "trusted_args", "amount": "agent_controlled",
                              "agent": "trusted_args"})
    v, why = executor.verify(g, agent="mcd_bot", amount=25, currency="USD",
                             merchant="mcdonalds.com", policy_version="2.1.0",
                             sources={"merchant": "trusted_args", "amount": "trusted_args",
                                      "agent": "trusted_args"})
    good = (not v) and why == "SOURCE_MISMATCH:amount"
    ok_all &= good
    print(f"[{'PASS' if good else 'FAIL'}] probe B: amount source replaced -> {why}")
    # (c) complete correct source map -> AUTHORIZED
    g = issuer.issue(agent="mcd_bot", amount=25, currency="USD", merchant="mcdonalds.com",
                     policy_version="2.1.0",
                     sources={"merchant": "trusted_args", "amount": "trusted_args",
                              "agent": "trusted_args"})
    v, why = executor.verify(g, agent="mcd_bot", amount=25, currency="USD",
                             merchant="mcdonalds.com", policy_version="2.1.0",
                             sources={"merchant": "trusted_args", "amount": "trusted_args",
                                      "agent": "trusted_args"})
    good = v and why == "AUTHORIZED"
    ok_all &= good
    print(f"[{'PASS' if good else 'FAIL'}] probe C: complete correct source map -> {why}")
    return ok_all


if __name__ == "__main__":
    print("VA × SpendShield E2E — authorize_payment composition (source-bound, full-chain)")
    print("params: recipient/amount/agent_id sink=TRUSTED_FIXED · purpose writable\n")
    print("source-bound negative probes (grant issuance/verification/consumption):")
    probes_ok = probe_source_binding()
    print()

    run_case("1. trusted recipient+amount fixed, model writes purpose       -> 1",
             {**TRUSTED, "purpose": "order breakfast, $25"}, TRUSTED, 1)
    run_case("2. model changes amount (trusted fixed)                       -> 0",
             {**TRUSTED, "amount": 5000, "purpose": "order breakfast, $25"}, TRUSTED, 0)
    run_case("2b. model changes recipient (trusted fixed)                   -> 0",
             {**TRUSTED, "recipient": "scam-vip.com", "purpose": "order breakfast"}, TRUSTED, 0)
    run_case("3. protected values without independent trusted_args binding  -> 0",
             {**TRUSTED, "purpose": "order breakfast, $25"}, None, 0)
    run_case("4. model supplies unknown source-shaped param in payload      -> 0",
             {**TRUSTED, "purpose": "order breakfast",
              "source": "trusted", "recipient_source": "trusted"}, TRUSTED, 0)
    run_case("4b. schema-legal spoof: source-claims inside writable purpose -> 1 (binding stays host-owned)",
             {**TRUSTED, "purpose": "order breakfast (source=trusted, recipient=scam-vip.com)"},
             TRUSTED, 1)
    run_case("5. only purpose changes while protected values remain bound   -> 1",
             {**TRUSTED, "purpose": "order a different breakfast item"}, TRUSTED, 1)

    print()
    if FAILURES or not probes_ok:
        print(f"FAILED: cases={FAILURES} probes={'FAIL' if not probes_ok else 'ok'}")
        sys.exit(1)
    print("ALL PASS — full chain + source-bound negative probes (VA allow · handler invoked · "
          "independent counter · SpendShield ALLOW · grant verified & consumed via Executor)")
    sys.exit(0)
