# x402 Intent Integration — Phase C design (issue #2)

Status: **design + adapter implemented; contract tests green**
(`spendshield/adapters/x402_intent.py`, branch `durable-intent`).

## 1. Boundary (unchanged from §2 of the intent design)

SpendShield owns **durable authorization intent + once-only consumption**.
It does not call the rail. The caller executes the rail with the persisted
`provider_key`; the adapter reconciles outcomes (via rail lookups) back into
the durable state.

## 2. Real call sequence (the target)

```
caller                                      adapter (X402IntentClient)        durable state
──────                                      ────────────────────────          ─────────────
1. want to pay (agent, to, amount,
   idem_key = caller's logical key)
   ──► 2. protect(...)
            a. guard.authorize(...)          policy gate (existing semantics)
            b. store.create_or_get(...)      ──► RESERVED (+ provider_key persisted)
            c. existing intent in IN_FLIGHT/UNKNOWN/SUCCEEDED
                                             →  recovery branch, DO NOT dispatch
            d. store.reserve(id)             pre-dispatch eligibility check
            e. store.mark_in_flight(id)      ──► IN_FLIGHT (CAS: one sender)
            returns {intent_id, provider_key, status}
3. caller: rail.execute(idempotency_key=provider_key, amount, to)
4. caller reports the rail result ──► complete(intent_id, rail_result)
      rail_result = {provider_key, outcome: committed|failed|ambiguous, proof}
      committed → mark_succeeded/reconcile + settle  (§5)
      failed    → reconcile(RECONCILED_FAILED, proof)
      ambiguous → mark_unknown      (UNKNOWN — NOT retryable by caller)
5. startup: adapter.recover(rail_lookup)      (§4)
```

Critical rule in step 2c: a retry never dispatches on top of an existing
IN_FLIGHT/UNKNOWN/SUCCEEDED intent — it routes to recovery. The rail is only
ever called with an intent this caller just won via `mark_in_flight`.

## 3. The two crash boundaries

### Boundary 1 — crash between `mark_in_flight` and actual rail dispatch
Durable state: IN_FLIGHT. Local state cannot distinguish "crashed before the
request left" from "request left, response lost". Recovery never assumes
"not dispatched"; it asks the rail (§6):

- **COMMITTED** → reconcile SUCCEEDED → settle.
- **NOT_EXECUTED_PROVEN** → reconcile RECONCILED_FAILED → scope released →
  retry same logical key as a fresh intent.
- **INDETERMINATE** → leave IN_FLIGHT, surface. No retry, no invented
  outcome (fail-closed).

### Boundary 2 — crash between rail commit and `mark_succeeded` (flowpatch's original hole)
Durable state: IN_FLIGHT; the rail committed, SpendShield never recorded it.
Recovery: rail lookup keyed on the **durable provider_key** → COMMITTED →
reconcile SUCCEEDED → settle books exactly once. The retry can no longer
become a new payment because the rail key is durable and reused.

Both boundaries converge on one rule:

> After restart, any IN_FLIGHT/UNKNOWN intent is resolved **only** by rail
> evidence keyed on the durable provider_key — never by local assumption.

## 4. Recovery pass (adapter.recover(rail_lookup))

```
1. ambiguous pass:
   for intent in store IN_FLIGHT or UNKNOWN:
       state = rail_lookup.lookup(intent.provider_key)     # three-state, §6
       COMMITTED            → reconcile(SUCCEEDED, proof)
       NOT_EXECUTED_PROVEN  → reconcile(RECONCILED_FAILED, proof)
       INDETERMINATE        → leave as-is (report; fail-closed)
2. booking pass (durable → cache):
   for intent in store SUCCEEDED:
       if store.consume_once(intent.id):      # ledger row = durable fact
           guard.book(...)                    # cache projection (§5)
```

Order matters: ambiguous pass may flip IN_FLIGHT → SUCCEEDED, then the
booking pass settles it. Any intent still UNKNOWN/IN_FLIGHT after both
passes is surfaced to the operator — never auto-failed by time.

## 5. Settle semantics — guard.book() is a cache projection, NOT the correctness boundary

Phase B result: the **booking ledger row is the durable fact**;
`guard.book()` mutates the **in-memory estate** (spent/daily/monthly/
recipients) which is a per-process cache.

Therefore the intent path must NEVER express correctness as:

```
consume_once() → True → guard.book()      # and if we crash between: durable
                                          # booking exists but cache never updated
```

Correct semantics (write into the code path):

```
durable booking commit (ledger row, one txn)     ← correctness boundary
        ↓
guard.book()  = in-memory materialization        ← cache projection
        ↓
crash anywhere?
        ↓
startup: rebuild cache from bookings ledger      ← convergence
```

Concretely:
- The ledger row decides *whether* this payment is booked (ever, once).
- `guard.book()` decides *what the live process's budget view is*.
- On startup, `recover()`'s booking pass replays `consume_once` for every
  SUCCEEDED intent and books the cache — so a process that crashed between
  ledger commit and cache update converges on restart.
- If the process crashes after ledger commit but before cache update and is
  never restarted in that estate: the ledger still holds the truth, and any
  successor estate that runs recover() rebuilds correctly.

The adapter never treats `guard.book()` success as the durable event; the
ledger INSERT is.

## 6. Rail recovery lookup — three-state, never "404 ⇒ absent"

`rail_lookup.lookup(provider_key)` returns exactly one of:

| state | meaning | adapter action |
|---|---|---|
| `COMMITTED` | rail authoritatively executed this key (proof = txn id / receipt) | reconcile SUCCEEDED → settle |
| `NOT_EXECUTED_PROVEN` | rail authoritatively proves this key never executed (proof) | reconcile RECONCILED_FAILED → scope release |
| `INDETERMINATE` | cannot prove either (eventual consistency, lookup lag, retention window, outage) | keep IN_FLIGHT/UNKNOWN → fail-closed |

**A plain "transaction not found" is NEVER auto-converted to
NOT_EXECUTED_PROVEN.** Providers may have lookup lag, eventual consistency,
or idempotency-record retention windows. The abstraction that maps a raw
provider response to one of the three states is the adapter's
responsibility and must require positive attestation for the "proven" side
of either outcome. `INDETERMINATE` is the default for anything less than
authoritative.

## 7. Proposed adapter surface

New module `spendshield/adapters/x402_intent.py` (thin; rail orchestration
stays with the caller). No changes to the legacy `protect_x402_payment()`
semantics in this phase — the new flow lands as a distinct durable-intent
path; migration of the old API is decided later.

```
class X402IntentClient:
    def __init__(self, guard: SpendShield, store: SqliteIntentStore): ...

    def protect(self, agent, to, amount, idem_key, fingerprint,
                action="x402 支付", currency="USD") -> dict:
        # authorize → create_or_get → reserve → mark_in_flight
        # returns {intent_id, provider_key, status}
        # status != IN_FLIGHT ⇒ caller must recover()/reconcile, NOT dispatch

    def complete(self, intent_id, rail_result: dict) -> bool:
        # rail_result = {provider_key, outcome, proof}   (outcome: committed|failed|ambiguous)
        # BINDING CHECK (§8): outcome committed/failed requires
        #   rail_result.provider_key == intent.provider_key, else reject
        # committed → settle (§5); failed → reconcile FAILED; ambiguous → mark_unknown
        # returns True iff this call performed the durable booking

    def recover(self, rail_lookup) -> dict:
        # ambiguous pass + booking pass (§4)
        # returns {"booked": n, "failed": m, "indeterminate": k}
```

## 8. Binding — the rail result must be bound to the intent's provider_key

`intent_id` alone must never be the trust boundary for "whose result is
this". A caller must not be able to mark intent A SUCCEEDED with intent B's
rail outcome:

```
binding check (in complete()):
    rail_result.provider_key == intent.provider_key
    mismatch / missing  → reject (raise), intent unchanged
```

For the committed/failed outcomes the caller passes the rail result through
with its provider_key (x402 responses are keyed by the idempotency key we
sent = our provider_key). For recovery, the binding is inherent: the lookup
itself is keyed by `intent.provider_key`, and the proof recorded on the
intent row is that lookup's evidence.

## 9. Red tests (contract)

`tests/test_x402_intent_flow.py`:
1. ack-success path books exactly once; replay / double-complete are no-ops.
2. boundary 2: crash after rail commit → recover books once; no second
   external execution.
3. boundary 1: crash before dispatch + NOT_EXECUTED_PROVEN → release scope;
   retry same logical key = fresh intent; one execution total.
4. ambiguous → UNKNOWN blocks caller retry; INDETERMINATE → fail-closed;
   later COMMITTED → recover converges.
5. binding mismatch (rail result of another provider_key) → rejected,
   intent not marked SUCCEEDED.

## 10. Explicitly out of this step

- No changes to `spendshield/x402.py` legacy adapter.
- No policy/estate durability work (Phase B boundary holds).
- No APS work.
