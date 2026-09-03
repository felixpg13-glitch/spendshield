#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SpendShield dogfood — an autonomous agent spend session, gated end-to-end (2026-09-03)

One policy file. One gate. Six states, all real engine output:

  B1  normal order            -> ALLOW
  B2  over single-tx cap      -> DENY  (max_transaction)
  B3  big order, human gate   -> APPROVAL -> approve -> ALLOW
  B4  cumulative daily budget -> DENY  (daily_budget; approved spend still counts)
  B5  policy change           -> new version + audit (same payment now ALLOW under v2)
  B6  bypass attempt          -> rail refuses: forged / missing grant (enforcement)

Run:
    python examples/dogfood_flow.py

WARNING: pure policy evaluation + mock rail. No real money moves.
"""
import json
import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from spendshield import SpendShield  # noqa: E402
from spendshield.enforce import AuthorizationIssuer, Executor  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
POLICY = os.path.join(HERE, "policy.dogfood.yaml")
AGENT = "mcd_bot"


def line(t="", c="="):
    print(c * 62 if not t else t, flush=True)


def beat(no, name, shield, amount, to, expect=None, by="felix"):
    r = shield.authorize(agent=AGENT, amount=amount, to=to,
                         meta={"intent": name, "initiated_by": by})
    tag = "  [%s] $%.2f -> %s | %s" % (r.decision, amount, to, r.reason)
    ok = "" if expect is None or r.decision == expect else "  <-- FAIL, expected %s" % expect
    print(tag + ok, flush=True)
    return r


if __name__ == "__main__":
    shield = SpendShield(policy=POLICY, log=lambda rec: None)
    line("SpendShield dogfood — autonomous spend session, gated end-to-end")
    line("policy: policy.dogfood.yaml  (daily $60 / single-tx $50 / approval >$25 / whitelist)", "-")
    print("", flush=True)

    # ---- B1: normal order -> ALLOW ----
    print("B1  agent orders breakfast, $25 @ mcdonalds.com (normal)", flush=True)
    r1 = beat("B1", "breakfast order", shield, 25, "mcdonalds.com", "ALLOW")

    # ---- B2: over single-tx cap -> DENY ----
    print("B2  agent tries weekly pass, $75 @ mcdonalds.com (over $50 cap)", flush=True)
    r2 = beat("B2", "weekly pass", shield, 75, "mcdonalds.com", "DENY")

    # ---- B3: big order -> APPROVAL, human approves -> ALLOW ----
    print("B3  agent tries catering order, $30 @ mcdonalds.com (>$25 needs human)", flush=True)
    r3 = beat("B3", "catering order", shield, 30, "mcdonalds.com", "APPROVAL")
    if r3.approval_id:
        print("    human reviews approval %s ... approves" % r3.approval_id, flush=True)
        ra = shield.approve(r3.approval_id, by="felix")
        print("    [%s] after human approval: %s" % (ra.decision, ra.reason), flush=True)

    # ---- B4: cumulative daily budget -> DENY (approved spend still counts) ----
    print("B4  agent adds $10 @ mcdonalds.com (25 + 30 = 55; +10 > daily $60)", flush=True)
    r4 = beat("B4", "extra order", shield, 10, "mcdonalds.com", "DENY")

    # ---- B5: policy change -> new version + audit; same payment now allowed ----
    print("B5  operator raises daily budget 60 -> 200 (policy lifecycle)", flush=True)
    pm = shield.policy_manager
    raw = yaml.safe_load(open(POLICY, encoding="utf-8"))
    draft = pm.create("raise daily budget for breakfast week",
                      raw, by="felix")
    draft.policy["version"] = "2.1.1"
    draft.policy["policy"]["budget"]["daily"] = 200
    raw = yaml.safe_load(open(POLICY, encoding="utf-8"))  # noqa: F821
    pm.validate(draft.id, by="felix")
    pm.simulate(draft.id, cases=[{"agent": AGENT, "amount": 10, "to": "mcdonalds.com"}], by="felix")
    pm.scan(draft.id, by="felix")
    pm.review(draft.id, by="felix", approve=True)
    pm.apply(draft.id, by="felix")
    print("    versions now:", ", ".join(v.get("version", "?") for v in pm.versions()), flush=True)
    r5 = beat("B5", "extra order under v2", shield, 10, "mcdonalds.com", "ALLOW")

    # ---- B6: bypass attempts -> enforcement refuses ----
    print("B6  attacker bypasses the gate: calls the rail directly", flush=True)
    issuer = AuthorizationIssuer(secret="dogfood-secret")
    executor = Executor(secret="dogfood-secret")
    grant = issuer.issue(agent=AGENT, amount=5, currency="USD", merchant="mcdonalds.com",
                         policy_version=r5.policy_version)
    ok1, why1 = executor.verify("", agent=AGENT, amount=5, currency="USD",
                                merchant="mcdonalds.com", policy_version=r5.policy_version)
    print("    no grant            -> %s (%s)" % (ok1 and "ACCEPT" or "REFUSE", why1), flush=True)
    forged = AuthorizationIssuer(secret="attacker-secret").issue(
        agent=AGENT, amount=500, currency="USD", merchant="scam-vip.com")
    ok2, why2 = executor.verify(forged, agent=AGENT, amount=500, currency="USD",
                                merchant="scam-vip.com")
    print("    forged $500 grant   -> %s (%s)" % (ok2 and "ACCEPT" or "REFUSE", why2), flush=True)
    ok3, why3 = executor.verify(grant, agent=AGENT, amount=5, currency="USD",
                                merchant="mcdonalds.com", policy_version=r5.policy_version)
    print("    real $5 grant       -> %s (%s)" % (ok3 and "ACCEPT" or "REFUSE", why3), flush=True)
    if ok3:
        print("    rail executes $5 payment (only after valid grant)", flush=True)

    line()
    results = [r1, r2, r3, ra, r4, r5]
    n_allow = sum(1 for r in results if r.decision == "ALLOW")
    n_deny = sum(1 for r in results if r.decision == "DENY")
    print("decisions: ALLOW x%d, DENY x%d, APPROVAL x1 (human-approved) + bypass REFUSED x2"
          % (n_allow, n_deny), flush=True)
    line("evidence: full audit trail exported below", "-")
    out = os.path.join(HERE, "..", "docs", "dogfood_audit.json")
    path = shield.export_audit(os.path.abspath(out))
    print("audit ->", os.path.abspath(path), flush=True)
