# Show HN: I built a payment guardrail after my automation spent $15 on a "test order"

**TL;DR:** An open-source Python + MCP library that sits between AI agents and money. Spend-capped agent identity (KYA), four deterministic gates (dry-run, budget, amount limit, human approval), prompt-injection defense, encrypted secret vault. `pip install spendshield` — [GitHub](https://github.com/felixpg13-glitch/spendshield)

---

## The incident that started it

On Aug 9, 2026, my automation system ran a "test order." I sent `dry: true` in the body, expecting a price preview.

The server only honored `?dry=1` in the URL.

**4 orders of ¥99 ($15) went through. Charged. Real money, gone.**

I didn't lose much — but I realized something uncomfortable: *this exact bug is about to happen everywhere, at much larger scale.*

AI agents are starting to order food, buy compute, call paid APIs. Every one of those is a new place where a test flag can silently fail, a budget can be exceeded, a prompt-injected agent can spend without permission.

When AI starts spending real money, **who puts a gate in front of it?**

I turned my scar into a library.

## What it does

```python
from spendshield import SpendShield

guard = SpendShield(budget=200, dry_run=True, whitelist=["McDonald's"])

@guard.protect("order")
def place_order(amount, to):
    return call_real_api(amount, to)   # real payment code

place_order(amount=99, to="McDonald's")
# => DryRunBlocked: nothing executed. dry_run is the default.

guard.dry_run = False
for i in range(4):
    place_order(amount=99, to="McDonald's")
    # 3rd order blocked by BudgetExceeded
```

Four deterministic gates — dry-run, budget, per-transaction limit, human approval — plus full audit. Rules are code, not AI opinion. An agent cannot argue, trick, or inject its way past.

## The three pillars (what makes it more than a wrapper)

**1. Identity (KYA)** — every agent registers with its own budget, blacklist, rate limits. *Unregistered agents are denied by default.*

```python
guard.register_agent("mcd_bot", budget=50, max_amount=30,
                     blacklist=["unknown_vendor"], whitelist=["McDonald's"])
```

**2. Intent alignment (anti prompt-injection)** — new recipients and large amounts *always* require human sign-off. A prompt-injected agent trying to pay a stranger gets blocked at "human didn't approve."

**3. Secret vault** — keys encrypted at rest (AES-256), master key never on disk, access gated and audited. No more "private key in a config file."

## x402-ready

[x402](https://x402.org) is the emerging open payment protocol for the internet (HTTP 402) — how agents will pay for APIs. SpendShield already has an adapter: gate the payment *before* it settles.

```python
from spendshield.adapters.x402 import X402PaywallGuard

pw = X402PaywallGuard(guard)
pw.authorize_resource("weather-api", price="0.01", asset="USDC", pay_to="0x...")
```

x402 lets agents pay. SpendShield stops them paying recklessly.

## State of the project

- 36 tests, identity/intent/vault/gates all covered
- MCP server for Claude Code / OpenClaw / any MCP client
- Dockerfile included
- MIT licensed

This is a 0-star project *today*. It was built in one day, from a real scar, for a problem I'm confident is coming.

If you've ever been burned by a "test order" — or you're building agents that will touch money — I'd love your feedback.

⭐ [GitHub: felixpg13-glitch/spendshield](https://github.com/felixpg13-glitch/spendshield)
📦 `pip install spendshield`
