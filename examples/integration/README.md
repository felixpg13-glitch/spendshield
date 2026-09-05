# Integration patterns — plug SpendShield into your agent stack

> Every pattern is the same shape: **your agent / payment tool → SpendShield → actual execution**.
> SpendShield never holds money. It sits in front of your existing rail (x402, Stripe, wallet, SDK call)
> and returns **ALLOW / APPROVAL / DENY** with a signed, single-use grant that your execution layer requires.
>
> All code below runs against the repo checkout (`python3 examples/...`) — no network, no real money.

## Pattern 1 — x402 payment (client or paywall)

```text
agent ── pay request ──► SpendShield (policy) ──► x402 submit ──► settlement
                          │ ALLOW / APPROVAL / DENY
```

Adapter: `spendshield/adapters/x402.py` — `X402PaywallGuard` (server/paywall side:
every resource payment passes gates before settlement) + `protect_x402_payment`
(client side: gate before submitting, confirm after settlement).

Run: `python3 -c "import spendshield.adapters.x402"` — see the module docstring for both shapes.
Channel-agnostic proof: `python3 examples/three_channel_demo.py` (same policy, x402 / Stripe / Alipay — policy code never changes).

## Pattern 2 — an agent that calls a payment-capable tool

```text
agent ── tool call ──► SpendShield.authorize() ──ALLOW + grant──► executor: verify + consume grant ──► rail
                          │ DENY → tool call refused, nothing executes
```

This is the pattern for LangChain / OpenAI function-calling / any agent-with-tools setup.
The key property: **execution requires the grant** (`spendshield/enforce.py` — `AuthorizationIssuer.issue`,
`Executor.verify`). A bare policy check is advice an agent can ignore; the executor refuses anything
without an unconsumed grant. Same-token replay → refused (one-time grant).

Run: `python3 examples/execution_gateway_demo.py` — shows ALLOW→executes, DENY→no grant,
replay→REFUSED. LLM-specific variant: `python3 examples/openai_guard.py` (budget / per-call cap /
rate limit / audit around an OpenAI call). Full agent story: `python3 examples/e2e_mcd_demo.py`.

## Pattern 3 — expose SpendShield as an MCP tool for any MCP client

```text
Claude Code / any MCP host ──► spendshield-mcp (tools: authorize_payment, spend_authorize, ...) ──► policy
```

Run as a stdio MCP server (`uvx --from spendshield spendshield-mcp --policy policy.yaml`).
Client configs: `docs/examples/claude_code_mcp.json` · connect guide: `docs/connect.html` on the
[project site](https://felixpg13-glitch.github.io/spendshield/connect.html).

## What an integration project needs to do (the minimal diff)

1. Put one `authorize()` (or the adapter) call between your agent and your payment execution.
2. On ALLOW: consume the signed grant in your executor (or call `confirm()` after settlement).
3. On DENY / APPROVAL: don't execute. That's it — budgets, caps, merchant lists, human approval
   and audit then come from the YAML policy, not from your code.

Policy example: `examples/policy.demo.yaml`. Full docs: root `README.md`, `docs/policy.md`.
Questions / integration help: open a GitHub issue — we answer fast and ship adapter PRs for real stacks.
