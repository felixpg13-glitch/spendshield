# x402 Intent Integration — Phase C design (issue #2)

Status: **integration design — no adapter code yet.** Builds on the frozen
Phase B primitive (`spendshield/intent.py`, branch `durable-intent`,
commit `4ccaae8`). Red contract tests live in
`tests/test_x402_intent_flow.py`.

## 1. Boundary (unchanged from §2 of the intent design)

SpendShield owns **durable authorization intent + once-only consumption**.
It does not call the rail. The caller executes the rail with the persisted
`provider_key`; a reconciler (adapter-owned, rail lookups) reports outcomes
back into the durable state.

## 2. Real call sequence (the target)

```
caller                                      adapter (X402IntentClient)        durable state
──────                                      ────────────────────────          ─────────────
1. want to pay (agent, to, amount,
   idem_key = caller's logical key)
   ──► 2. protect(...)
            a. guard.authorize(...)          policy gate (identity/budget/
                                               approval) — existing semantics
            b. store.create_or_get(...)      ──► RESERVED (+ provider_key persisted)
            c. if returned existing intent with status
               IN_FLIGHT/UNKNOWN/SUCCEEDED   →  recovery branch (§4), DO NOT dispatch
            d. store.reserve(id)             pre-dispatch eligibility check
            e. store.mark_in_flight(id)      ──► IN_FLIGHT (CAS: one sender)
            returns {intent_id, provider_key}
3. caller: rail.execute(idempotency_key=provider_key, amount, to)
4. caller reports outcome ──► complete(intent_id, rail_result)
      committed          → mark_succeeded → settle (consume_once → guard.book)
      explicit failure   → reconcile(RECONCILED_FAILED, proof=rail_error)
                           (rail authoritatively says not executed)
      timeout/network err→ mark_unknown   ──► UNKNOWN (NOT retryable by caller)
5. startup: adapter.recover()  (§4)
```

Critical rule encoded in step 2c: **a retry never dispatches on top of an
existing IN_FLIGHT/UNKNOWN/SUCCEEDED intent.** It routes to recovery first.
The rail is only ever called with an intent in RESERVED that this caller just
won via `mark_in_flight`.

## 3. The two crash boundaries (review focus)

### Boundary 1 — crash between `mark_in_flight` and actual rail dispatch
Durable state: **IN_FLIGHT**. Local state cannot distinguish "crashed before
the request left" from "request left, response lost" — both look like
IN_FLIGHT after restart. Therefore recovery must NOT assume "never
dispatched"; it asks the rail:

- rail lookup by `provider_key` → **absent (authoritative)** → reconcile
  RECONCILED_FAILED → scope released → caller may retry the same logical key
  as a fresh intent. Safe: rail authoritatively never saw the old key.
- rail lookup → **committed** → reconcile SUCCEEDED → settle (this is really
  boundary 2).
- rail **unreachable** → leave IN_FLIGHT, surface to operator. **No retry,
  no invented outcome** (fail-closed). The tiny pre-send window costs an
  operator round-trip in the worst case, never a double payment.

### Boundary 2 — crash between rail commit and `mark_succeeded` (flowpatch's original hole)
Durable state: **IN_FLIGHT** (the commit happened at the rail, SpendShield
never recorded it). Restart recovery:

- rail lookup by `provider_key` → **committed** → reconcile SUCCEEDED
  (proof = rail txn id) → `consume_once` books it exactly once.
- This is the hole the issue reported: previously the retry generated a new
  rail key and paid twice. Now `provider_key` is durable and the lookup is
  keyed on it, so the retry cannot silently become a new payment.

Both boundaries converge on one rule:

> After restart, any IN_FLIGHT/UNKNOWN intent is resolved **only** by rail
> evidence keyed on the durable provider_key — never by local assumption.

## 4. Recovery pass (adapter.recover())

Run on startup / before any retry, in this order:

```
1. ambiguous pass:
   for intent in store IN_FLIGHT or UNKNOWN:
       evidence = rail.lookup(intent.provider_key)
       committed  → reconcile(SUCCEEDED, proof=txn)        → settle
       absent     → reconcile(RECONCILED_FAILED, proof=absent)
       unreachable→ leave as-is (fail-closed; report)
2. booking pass:
   store.replay_succeeded()   # books any SUCCEEDED not yet booked
                              # (crash between rail-ok and settle)
```

Order matters: the ambiguous pass may flip IN_FLIGHT → SUCCEEDED, then the
booking pass settles it. Any intent still UNKNOWN after both passes is
surfaced to the operator — it is never auto-failed by time (UNKNOWN
invariant, per Phase B review).

## 5. Proposed adapter surface (for design review — not implemented)

New module `spendshield/adapters/x402_intent.py` (thin; rail orchestration
stays with the caller):

```
class X402IntentClient:
    def __init__(self, guard: SpendShield, store: SqliteIntentStore): ...
    def protect(self, agent, to, amount, idem_key, fingerprint,
                action="x402 支付") -> dict:
        # authorize → create_or_get → reserve → mark_in_flight
        # returns {intent_id, provider_key, status}   (status == "IN_FLIGHT")
    def complete(self, intent_id, rail_result: str, proof: str = "") -> bool:
        # rail_result: "committed" | "failed" | "ambiguous"
        # committed → mark_succeeded → consume_once → guard.book (returns booked)
        # failed    → reconcile(RECONCILED_FAILED, proof)
        # ambiguous → mark_unknown
    def recover(self, rail_lookup) -> dict:
        # ambiguous pass + booking pass; returns report
```

`guard.book()` here plays its documented v1 role: **in-memory estate cache
update** after the durable booking commit — the ledger row is the durable
fact, `guard.book` makes budget enforcement work in the live process.

## 6. Red tests (contract, failing until module exists)

`tests/test_x402_intent_flow.py`:
1. ack-success path books exactly once; replay is a no-op.
2. crash between rail-commit and mark_succeeded (boundary 2) → recover
   books once; no second rail execution.
3. crash before dispatch + rail absent (boundary 1) → recover releases
   scope; retry with same logical key is a fresh intent; no booking.
4. ambiguous timeout → caller retry is blocked (no new dispatch); rail
   unreachable → stays UNKNOWN, nothing booked/failed (fail-closed).
5. happy path double `complete("committed")` → second settle is a no-op.

## 7. Explicitly out of this step

- No changes to `spendshield/x402.py` legacy adapter yet.
- No policy/estate durability work (Phase B boundary holds).
- No APS work.
