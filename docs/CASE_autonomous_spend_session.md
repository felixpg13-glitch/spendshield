# Case: an autonomous spend session, gated end-to-end

> One policy file. One gate. Six states. All real engine output — reproducible in ~3 seconds.

## Reproduce

```bash
pip install spendshield
git clone https://github.com/felixpg13-glitch/spendshield
cd spendshield
python examples/dogfood_flow.py
```

No account, no API key, no money moves (policy evaluation + mock rail).

## What the run shows

| # | the agent asks to | decision | why |
|---|---|---|---|
| 1 | pay $25 to mcdonalds.com | **ALLOW** | within single-tx cap, within daily budget, merchant whitelisted |
| 2 | pay $75 to mcdonalds.com | **DENY** | `transaction $75.00 exceeds the $50.00 limit` |
| 3 | pay $30 to mcdonalds.com | **APPROVAL → ALLOW** | over the $25 human-approval threshold; human approved |
| 4 | pay $10 more (55 + 10 > 60) | **DENY** | `daily spend $55.00 + $10.00 exceeds the $60.00 daily budget` — human approval does not override the independent budget constraint |
| 5 | operator raises daily budget 60 → 200 | **version 2.1.0 → 2.1.1** | policy lifecycle (create → validate → simulate → scan → review → apply); the same $10 request re-evaluates to ALLOW under the new policy |
| 6 | bypass: call the rail directly | **REFUSED ×2** | no grant → `MALFORMED_TOKEN`; forged $500 grant → `INVALID_SIGNATURE`. A real grant verifies → `AUTHORIZED`, then the rail executes |

## Evidence artifacts

- Full run output: `docs/dogfood_flow_output.txt`
- Tamper-evident hash chain: `docs/dogfood_audit_chain.json` — 12 events, chain `INTACT`
  (5 authorizations + 1 approval + 6 policy-lifecycle events: create / validate / simulate / scan / review / apply)
- Audit records: `docs/dogfood_audit.json`

## What this is (and isn't)

This is SpendShield gating one realistic agent spend session: decisions, escalation to a human, mid-session policy change, and enforcement at the rail — all audited in a hash chain.

It is not a payment rail, a wallet, or a promise. SpendShield never holds keys or money; it decides whether a payment is authorized *before* the signing/payment code runs.
