---
title: "We Gave an AI Agent Permission to Spend Money. Then We Tried to Break It."
description: "Why a spending cap isn't authorization — an experiment report from dogfooding a policy + enforcement layer for AI agents (259 tests, 11,351 adversarial attempts, reproducible)."
tags: [ai, agents, security, python, mcp]
cover_image: https://raw.githubusercontent.com/felixpg13-glitch/spendshield/main/docs/spendshield_e2e_mcd.gif
---

# We Gave an AI Agent Permission to Spend Money. Then We Tried to Break It.

AI agents are increasingly able to call APIs, purchase things, and trigger actions that have real financial consequences.

So we asked a simple question:

**What happens if the agent is allowed to spend money — and we actively try to make it violate the rules?**

We built [SpendShield](https://github.com/felixpg13-glitch/spendshield) as an authorization and enforcement layer for agent spending, then dogfooded it against the failures we were most worried about. This is the experiment report — what broke, what held, and what we still don't know.

---

## 1. The first lesson: a spending cap is not a spending policy

`$100/day` tells you *how much* an agent can spend. It doesn't tell you:

- **who** is allowed to spend it,
- **what** it can be spent on,
- **which** merchant or provider is allowed,
- **under what conditions**,
- or whether the authorization **actually reaches the execution layer**.

Cap vs policy is not a semantic nitpick:

```text
Cap:     "≤ $100"
Policy:  "Agent X can spend ≤ $20 on provider Y for purpose Z during this window"
```

A cap is a number. A policy is a decision with structure — dimensions, conditions, and a human approval path. The second thing we learned is that even a structured policy is only *advice* unless execution is forced to respect it.

## 2. The experiment setup

We gave an agent a real spending permission with one YAML policy:

- daily budget, single-transaction cap, merchant allowlist/blocklist,
- human approval above a threshold,
- per-day "benefit" limits (one breakfast order per day),
- a tamper-evident audit chain over *everything*, including policy changes.

Then we ran it through a realistic autonomous session and recorded every decision. The policy was the same file the whole time; we only changed what the agent asked for and how it asked.

## 3. What happened when we tried to break it

We attacked our own layer the way we'd attack someone else's. Six highlights from the run:

| the agent asked to | decision | why |
|---|---|---|
| pay $25 to mcdonalds.com | ALLOW | within cap, budget, whitelist |
| pay $75 (cap is $50) | DENY | single-transaction limit |
| pay $30 (> human threshold) | APPROVAL → ALLOW | human approved |
| pay $10 more (spent $55, budget $60) | DENY | **a human approval doesn't override the budget** |
| same $10 after operator raised budget | ALLOW | policy versioned 2.1.0 → 2.1.1, re-evaluated |
| call the rail directly, no grant | REFUSED | execution requires a valid grant |

The interesting failure mode is the fourth row: an *approved* payment still counts against the budget. Approval is not a license to exceed policy — a lesson that only shows up when you test the states in combination, not one at a time.

## 4. Then we ran 11,351 adversarial attempts

Beyond the scripted session, we fuzzed and attacked the decision layer itself: malformed inputs, boundary values, type confusion, replayed requests, concurrent spending races, policy lifecycle edge cases (approve a payment under a loose policy, tighten the policy, try to execute the old approval).

Results:

- **11,351 adversarial attempts → 0 unintended ALLOW, 0 crashes** (current corpus — more below)
- The suite found real bugs, including one we'd shipped: a `bool`-typed input that could be coerced into an ALLOW. It's fixed and locked in as a regression test. Finding your own shipped bug this way is exactly why we do it.
- One serious case (policy tightened *after* an approval was pending, old approval still honored) was caught, fixed, and added to the attack corpus as `AUTH-001`.

Every attack we found became a numbered entry in a corpus with its minimal repro, the invariant it violated, and the regression test that now guards it. That corpus is the asset we care most about — features get copied; a documented attack history with failing-then-passing tests does not.

## 5. The distinction that matters most

```text
Agent → Authorization → Policy decision → Enforcement → Payment execution → Audit
```

**Authorization that doesn't reach execution is only advice.** So SpendShield issues a *signed, single-use grant*, and the execution layer is built to consume it:

```text
SpendShield:  policy → ALLOW → signed grant (agent · amount · merchant · policy version)
Execution:    verify(grant) → valid + unused → execute
              otherwise     → fail closed
```

Reproducible behavior from the demo:

```text
call 1 (valid grant)    → EXECUTES
call 2 (same token)     → REFUSED (REUSED)
no grant                → REFUSED (MALFORMED_TOKEN)
forged $500 grant       → REFUSED (INVALID_SIGNATURE)
tampered grant          → REFUSED (INVALID_SIGNATURE)
```

One execution, four refusals. A policy check an agent can ignore is an opinion; a grant an execution layer refuses to move money without is a gate.

## 6. Dogfooding is not a nice-to-have

SpendShield started from a real incident: a "dry run" flag that was silently ignored, and a test order that charged real money. That experience is why we don't trust happy-path demos. Dogfooding our own layer surfaced a genuine second-order bug: the audit chain was complete internally, but the export path omitted policy-lifecycle events — so a compliance export would have been missing exactly the "who changed the rules and when" records. Systems that are actually used expose these; systems that are only demonstrated don't.

## 7. What we're still unsure about (honest version)

We are not claiming agent-payment safety is solved. Open unknowns:

- **Replay protection today is in-process memory.** Cross-process / distributed enforcement needs a shared, durable consumed-grant store — a production TODO, not a solved problem.
- **Real rails differ.** We've tested against a mock gateway. Stripe, x402, and card rails each have their own failure semantics; integration is where the next bugs live.
- **Adversarial agents are an open frontier.** 11,351 attempts is evidence about our current corpus, not a proof of safety. Novel attacks will appear; that's why the corpus grows.
- **Adoption is unvalidated.** We have no external users yet. The engineering held up to our attacks; whether the *category* is needed is a market question, and we're trying to learn it rather than assume it.

## 8. Try it

Everything above is reproducible in about ten seconds:

```bash
pip install spendshield
git clone https://github.com/felixpg13-glitch/spendshield
cd spendshield
python examples/execution_gateway_demo.py   # the 1-execute / 4-refusals demo
python examples/dogfood_flow.py             # the 6-state session, hash-chain audit
```

- Repo: [github.com/felixpg13-glitch/spendshield](https://github.com/felixpg13-glitch/spendshield)
- PyPI: [pypi.org/project/spendshield](https://pypi.org/project/spendshield)
- MCP Registry: `io.github.felixpg13-glitch/spendshield`

And if you want to try breaking it yourself: the repo has a **Break the Gate** security challenge — find an unauthorized path to ALLOW and it goes in the corpus. We'd rather you find the next bug than wait for an agent with your credit card to.
