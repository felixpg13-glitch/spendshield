# 💰 SpendShield — the policy control plane between AI agents and money

> **Before your AI spends real money, it passes through SpendShield.**

> *Not a wallet, not a payment rail — the authority layer: does this agent have the right to make this payment?*

[![PyPI version](https://img.shields.io/pypi/v/spendshield)](https://pypi.org/project/spendshield/)
[![Tests](https://img.shields.io/badge/tests-229%20passing-brightgreen)](https://github.com/felixpg13-glitch/spendshield/actions)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

Agent wants to spend **$75**:

```text
AGENT ──► SpendShield ──► Policy: max $50
              │
              ▼
        ❌ DENY — transaction $75.00 exceeds the $50.00 limit
              │
              └── MAX_TRANSACTION_EXCEEDED · audited · policy v2.0.0
```

One YAML policy. One `authorize()` call. Every payment **decided, explained, audited** — with a reason an LLM can consume, and a tamper-evident audit chain.

[**▶ 30-second live demo**](https://felixpg13-glitch.github.io/spendshield/demo.html) — watch an AI agent get stopped.

## 📊 Status — v0.8.x · 229 tests passing

| Policy Lifecycle | Security |
|---|---|
| ✓ Validate | ✓ Fuzz testing (random-seed soak) |
| ✓ Simulate | ✓ Invariant testing (8-rule constitution) |
| ✓ Scan | ✓ MCP adversarial testing |
| ✓ Review | ✓ Tamper-evident audit chain |
| ✓ Apply · ✓ Version · ✓ Rollback | ✓ Attack corpus (45 cases) |

Not a demo — a working baseline. Every result in the demo is real engine output.


## ⚡ Quickstart — 5 minutes to running

```bash
pip install spendshield
```

**1. Write a policy** (`policy.yaml`):

```yaml
version: "2.0.0"
policy:
  budget:        { daily: 100, monthly: 1000 }   # hard ceilings
  transaction:   { max: 50 }                     # per-payment cap
  merchants:
    allowed: [amazon.com, walmart.com]           # exact domain match
    blocked: [scam-vip.com]
  approval:      { over: 30, new_merchant: true, channel: tg }  # human sign-off
agents:
  shopping-agent:
    transaction: { max: 50 }
```

**2. Gate your payment function**:

```python
from spendshield import SpendShield

shield = SpendShield()
shield.load_policy("policy.yaml")

@shield.protect("order", agent="shopping-agent")
def place_order(amount, to):
    return call_real_api(amount, to)   # denied / needs-approval raises before this runs
```

Or use the result object directly:

```python
result = shield.authorize("shopping-agent", 2000, "scam-vip.com")
print(result.decision)   # "DENY"
print(result.reason)     # "merchant 'scam-vip.com' is blocked"
```

**3. Watch it work** (real engine output):

```
❌ DENY
Reason: merchant 'scam-vip.com' is blocked
  - MERCHANT_BLOCKED: merchant 'scam-vip.com' is blocked (block)
Policy version: 2.0.0
```

## 🤖 MCP Quickstart — let the agent manage itself

```bash
pip install spendshield
spendshield-mcp --policy policy.yaml     # stdio MCP server, 16 tools
```

Claude Code / any MCP host gets: `spend_authorize`, `spend_approve`, `policy_sim`, `policy_apply`, `policy_create` → `policy_review` → `policy_lifecycle_apply`, `policy_rollback`… An agent can **ask "will this be denied?" before spending**, and humans approve the big ones.

## 🧪 How it's tested (real money → real discipline)

- **229 tests**, 14+ security suites: budget bypass, race conditions, replay, double-spend, parameter tampering, credential leaks…
- **Security constitution — 8 invariants** that must never break: unauthorized → no payment · over budget → no payment · approval mismatch → no payment · invalid identity → no payment · replay → at most one authorization · concurrency → never breaks budget · engine failure → deny · agent can't bypass SpendShield
- **Fuzz (random-seed soak)**: thousands of attack combinations per run, Money Invariant must hold
- **Audit hash chain**: every decision is an event chained by hash — tamper with history and it's detected
- Every discovered hole → permanent regression test. Release blocked on any P0/P1 security bug. Before each release we ask: *did this change give an attacker a new way to spend money?*

## 🗺️ Roadmap

```
V1 prevent reckless spending ✅ → V2 Policy Engine ✅ → V2.2 Security Harness ✅
→ v0.7.2 Known-Good baseline ✅ → 0.8 Policy Lifecycle ✅ (CREATE→VALIDATE→SIMULATE→SCAN→REVIEW→APPLY→ROLLBACK)
→ Reality Test (real agents, real money, real attacks) ← we are here
→ V3 Intent Layer → V4 Risk → V5 IAM → V6 Payment Rails → 1.0
```

**The metric that matters:** real agents protected, real transactions gated, real dollars saved — not stars.

## 🩸 Why this exists (a real incident)

On August 9, 2026, my automation ran a test order. I sent `dry: true` expecting a price preview — the server only honored `?dry=1`. **4 orders of ¥99 were charged for real. The money was gone.** When AI starts spending real money, who puts a gate in front of it? I turned my scar into a library.

## ⚠️ Transparent threat model

- MCP has no auth — trust your host; `policy_apply` / `policy_review` are host-level operations
- Approval IDs are 48-bit random — a library trusts its caller
- In-memory audit (append-only on the roadmap)
- **We are actively seeking real-world attacks**: [Reality Test](docs/REALITY_TEST.md) — challenge: *make a DENY turn into APPROVE*

---

**SpendShield: the layer I wish I had before my AI spent my money.**
