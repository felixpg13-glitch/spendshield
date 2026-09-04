# Migration Audit — legacy vs durable x402 flow (Phase C Step 3)

Scope: which public entry points exist for x402, which flow a user's default
path takes today (legacy in-memory vs durable intent), and the decision
matrix for the legacy API. No code changes in this step.

## A. Entry-point inventory

| # | Entry point | Where | Does it route to rail / booking? | Default flow today |
|---|---|---|---|---|
| 1 | Core `SpendShield` policy API (`authorize_payment`, MCP tools) | `spendshield/__init__.py`, `guard.py`, MCP server | Policy decision only — **no rail, no booking** | Safe: no execution inside SpendShield. This class of bug (ambiguous external execution) cannot occur here. |
| 2 | Legacy client helpers `protect_x402_payment()` / `confirm_x402_payment()` | `spendshield/adapters/x402.py` (module docstring shows usage) | Yes: authorize → caller rail → confirm → `guard.book()` (in-memory) | **Legacy, not restart-durable** — the flowpatch hole. Reachable only by explicit import of `spendshield.adapters.x402`. |
| 3 | `X402PaywallGuard` (server-side paywall: `authorize_resource` / `confirm_payment`) | same legacy module | Yes: gates a resource provider's settlement; `confirm_payment` → `guard.book()` (in-memory) | **Legacy semantics** (receiving side, not issue #2's client flow). Also in-memory booking; durable receiving-side adapter is future work. |
| 4 | New durable client `X402IntentClient` (`protect`/`complete`/`recover`) | `spendshield/adapters/x402_intent.py` | Yes: durable intent + ledger booking + recovery | **Durable** (issue #2 fix). Explicit import only — nothing routes here by default yet. |
| 5 | README / docs / landing (`docs/index.html`, `why.html`, articles) | repo docs | x402 appears only as a **conceptual downstream channel** (channel-agnostic diagrams/prose). No code example calls the legacy helpers. | Narrative-only: no migration pressure, but also no pointer to the durable flow. |
| 6 | `examples/` | `examples/three_channel_demo.py` etc. | x402 simulated inline (policy demo); none import the legacy adapter helpers. | Safe (demo of policy switching, not of booking). |
| 7 | Tests | `tests/test_x402_adapter.py` (legacy) + `test_x402_intent_flow.py` / `test_intent_exactly_once.py` (durable) | — | Both suites green; legacy suite pins current behavior. |
| 8 | PyPI/MCP packaging (`pyproject.toml`, `server.json`) | package discovery | adapters not exported at top level (`__init__` exports core only); MCP tools are policy-only. | No x402 rail exposed through MCP → MCP layer unaffected. |

**Conclusion**: the legacy client helpers are real but **not the default path for
most users** (not in README examples, not top-level exports, not MCP). The
danger is narrower than feared: users who explicitly adopt
`spendshield.adapters.x402` client helpers get the unsafe flow. But "not
prominent" ≠ "safe to ignore" — a fix that nobody reaches by default still
leaves a landmine for the next person who greps for x402.

## B. Legacy API decision matrix

| Option | Meaning | Verdict |
|---|---|---|
| **Deprecate** (recommended now) | Keep legacy behavior + tests; mark client helpers `deprecated`/"not restart-durable" in docstrings; README/docs point new users to `X402IntentClient` | ✅ **Do this now** — matches Felix instinct: call shapes already differ (`protect→rail→confirm` vs `protect→provider_key→rail→complete/recover`); a silent redirect would create a new compatibility crash window. |
| Redirect | Legacy calls internally migrate to the durable primitive | ⏸ Later, only with an explicit migration shim + its own tests; not silent. |
| Remove in major | Delete client helpers at next breaking release (v1.0) | ⏸ Note in docs as planned; do not remove pre-1.0. |
| Server-side `X402PaywallGuard` | Different role (receiving side); same in-memory booking caveat | 📌 Keep + document "legacy semantics; durable receiving-side adapter is future work" — out of issue #2 scope. |

## C. Recommended follow-ups (tracked, not built now)

1. Docstrings on legacy client helpers: mark deprecated + pointer to durable flow.
2. README: "Durable x402 flow (recommended)" section with `X402IntentClient`
   usage; legacy listed as deprecated.
3. Issue #2 evidence reply: e2e crash-simulation results (external
   executions == 1, durable bookings == 1 across restart/ack-loss/proven-
   absence/lookup-unavailable) — after `tests/test_e2e_durable_flow.py` is
   green.
4. Revisit at v1.0: remove legacy client helpers or ship a tested redirect
   shim.

## D. What this step does NOT do

- No code changes to legacy `x402.py`.
- No silent redirect, no removal.
- No new features (APS/MCP/etc.) — per gate discipline.
