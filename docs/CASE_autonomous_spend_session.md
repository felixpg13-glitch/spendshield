# What happens when an autonomous agent tries to exceed its spending policy?

One agent. One YAML policy. One gate. Six real decisions — every line below is engine output from a single run, reproducible in ~3 seconds.

| # | the agent asks to | decision | why |
|---|---|---|---|
| 1 | pay $25 to mcdonalds.com | ✅ **ALLOW** | within single-tx cap, within daily budget, merchant whitelisted |
| 2 | pay $75 to mcdonalds.com | 🚫 **DENY** | `transaction $75.00 exceeds the $50.00 limit` |
| 3 | pay $30 to mcdonalds.com | ⏸️ **APPROVAL → ALLOW** | over the $25 human-approval threshold; human approved |
| 4 | pay $10 more (spent $55, budget $60) | 🚫 **DENY** | `daily spend $55.00 + $10.00 exceeds the $60.00 daily budget` — a human approval does not override the independent budget |
| 5 | operator raises daily budget 60 → 200 | 🔄 **v2.1.0 → v2.1.1** | lifecycle: create → validate → simulate → scan → review → apply; the same $10 request now ALLOWs under the new policy |
| 6 | bypass: call the rail directly, no gate | 🔒 **REFUSED ×2** | no grant → `MALFORMED_TOKEN`; forged $500 grant → `INVALID_SIGNATURE`. Only a real grant verifies → `AUTHORIZED`, then the rail executes |

The whole session lands in a **tamper-evident hash chain** — 12 events (5 authorizations + 1 approval + 6 policy-lifecycle actions), chain verified `INTACT`.

## Reproduce it yourself

```bash
pip install spendshield
git clone https://github.com/felixpg13-glitch/spendshield
cd spendshield
python examples/dogfood_flow.py
```

No account, no API key, no money moves (policy evaluation + mock rail).

## Evidence artifacts

- Run output: [`docs/dogfood_flow_output.txt`](dogfood_flow_output.txt)
- Hash chain (12 events, INTACT): [`docs/dogfood_audit_chain.json`](dogfood_audit_chain.json)
- Audit records: [`docs/dogfood_audit.json`](dogfood_audit.json)

## What this is (and isn't)

This is SpendShield gating one realistic agent spend session: decisions, escalation to a human, mid-session policy change, and enforcement at the rail — all audited.

It is not a payment rail, a wallet, or a promise. SpendShield never holds keys or money; it decides whether a payment is authorized *before* the signing/payment code runs.
