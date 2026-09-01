# 💰 SpendShield — Policy & Authorization Layer for AI Payments

> **Let AI agents spend real money — without spending it recklessly.**

SpendShield is the **policy and authorization layer** between AI agents and money. It is not a wallet and not a payment processor: it sits at `Agent ↔ Money` and decides **should this payment happen?** using deterministic, explainable rules. Payment providers (Stripe, x402, wallets, cards) stay downstream — SpendShield never holds your money.

```text
        HUMAN
          │  delegation
          ▼
   AGENT IDENTITY ──► INTENT ──► POLICY ENGINE ──► RISK ──► AUTHORIZATION ──► APPROVAL
                                                                              │
                                                      Stripe / x402 / Wallet ◄─┘
                                                                              │
                                                                          REAL MONEY
                                                                              │
                                                                          AUDIT
```

## 🩸 Why this project exists (a real incident)

On August 9, 2026, my automation system ran a test order. I sent `dry: true`, expecting a price preview — the server only honored `?dry=1`. **4 orders of ¥99 were charged for real. The money was gone.**

This is not just my problem. AI agents are about to order food, top up accounts, and call paid APIs on your behalf. **When AI starts spending real money, who puts a gate in front of it?**

I turned my scar into a library.

## 🎯 Three-way decisions, not just allow/deny

Every authorization returns a structured result — the *reason* is part of the product:

```python
from spendshield import SpendShield

shield = SpendShield()
shield.load_policy("policy.yaml")

result = shield.authorize(agent="shopping-agent", amount=75, to="amazon.com")
```

```
❌ DENY
Reason: transaction $75.00 exceeds the $50.00 limit
  - max_transaction: transaction $75.00 exceeds the $50.00 limit (block)
```

| Decision | Meaning |
|---|---|
| ✅ **ALLOW** | Safe to pay. Budget is reserved. |
| ⏸️ **APPROVAL** | Needs human sign-off. `shield.approve(id)` re-evaluates with current rules. |
| ❌ **DENY** | Blocked, with structured `RuleHit`s explaining exactly why. |

## 📜 Policy DSL — rules as YAML

One policy, one place. Agent rules override global rules (CSS-like merge):

```yaml
version: "2.0.0"
policy:
  budget:        { daily: 100, monthly: 1000 }
  transaction:   { max: 50 }
  merchants:
    allowed: [amazon.com, walmart.com]      # exact domain match, subdomains ok
    blocked: []
  approval:      { over: 30, new_merchant: true, channel: tg }
  rate_limit:    { window_s: 3600, max_calls: 5, max_total: 300 }

agents:
  shopping-agent:
    transaction: { max: 50 }
```

**Before spending, simulate — never touches money:**

```python
from spendshield.policy import PolicySimulator

sim = PolicySimulator(policy_raw=policy)
sim.sweep("shopping-agent", "amazon.com", [20, 30, 50, 51, 80])
# {20: ALLOW, 30: ALLOW, 50: APPROVAL, 51: DENY, 80: DENY}
```

## 🛡️ Security Constitution — 8 invariants

The whole system is built around invariants that **must never be violated**, no matter how the code evolves:

1. Unauthorized → no payment
2. Over budget → no payment (`spent <= budget` always)
3. Approval mismatch → no payment
4. Invalid identity → no payment
5. Replay → at most one valid authorization
6. Concurrency → never breaks budget
7. Engine failure → deny by default (fail-closed)
8. An agent cannot obtain the ability to bypass SpendShield

V3+ layers (Intent, Risk) must not break these 8 rules.

## 🧪 Tested like it guards real money

- **186 tests**, all green — including **14 security/attack suites**
- 8 attack surfaces: budget_bypass, race_condition, replay_attack, double_spend, policy_bypass, approval_bypass, parameter_tampering, credential_leak
- **Security constitution tests** (the 8 invariants above, verified under fuzz + concurrency)
- **Fuzzing**: thousands of random attack combinations, Money Invariant must hold
- **Simulator ↔ real engine differential**: 800 random requests, decisions must match exactly
- **Migration property tests**: random V1 configs migrate without breaking intent
- Every discovered hole → fix → permanent regression test. Release discipline: any P0/P1 security bug blocks release. Before each release we ask: *"did this change give an attacker a new way to spend money?"*

## 🧩 MCP — for AI agents themselves

10 tools over stdio JSON-RPC (`python -m spendshield.mcp --policy policy.yaml`):

`spend_authorize` / `spend_approve` / `spend_reject` / `policy_sim` / `policy_apply` / `spend_protect` / `spend_status` / `spend_audit` / `spend_reset` / `secret_get`

## ⚠️ Threat model & known limits (transparent)

- **MCP has no authentication** — trust your host. `policy_apply` / `spend_approve` are host-level operations.
- **Approval IDs** are 48-bit random — a library trusts its caller.
- **In-memory audit** can be modified by code with process access (append-only audit is on the V8 roadmap).
- Denormal amounts (< 1e-9) are accepted but harmless (no money impact).

## 🗺️ Roadmap

```
V1 prevent reckless spending ✅ → V2 Policy Engine ✅ → V2.1 Simulator ✅
→ V2.2 Security Harness ✅ → engine switch ✅ → MCP ✅ → V2 Hardening 🔄
→ V3 Intent Layer (anti prompt-injection) → V4 Risk Engine (deterministic → ML)
→ V5 Agent Identity / Delegation (IAM) → V6 Payment Rails
→ V7 Dashboard → V8 Enterprise
```

**The metric that matters:** real agents protected, real transactions gated, real dollars saved. Not stars.

## 🚀 Quick start

```bash
pip install spendshield
```

```python
from spendshield import SpendShield

guard = SpendShield(dry_run=True)          # dry-run on by default
guard.load_policy("examples/policy.v2.yaml")

@guard.protect("order")
def place_order(amount, to):
    return call_real_api(amount, to)       # denied/needs-approval raises before this runs
```

V1-style constructor config still works — it is auto-migrated to the V2 engine:

```python
guard = SpendShield(budget=200, dry_run=True, whitelist=["McDonald's"])
```

## 🔑 Secret vault

Keys encrypted at rest (Fernet), master key never on disk, key access passes the same gates and is fully audited:

```python
from spendshield import SpendShield, KeyVault

vault = KeyVault("vault.json", master_key=os.environ["SPENDGUARD_MASTER_KEY"])
guard = SpendShield(key_vault=vault, approval="console")
sk = guard.get_secret("mcd_sk", agent="mcd_bot")   # gated + audited
```

---

**SpendShield: the layer I wish I had before my AI spent my money.**
