# Durable Intent Layer — design (issue #2, pre-implementation)

Status: **design for review — no code yet.** Drives the 5 RED tests in
`tests/test_intent_exactly_once.py` on branch `durable-intent`.

## 0. The invariant

One logical payment = at most one external execution + at most one local
booking — across ack loss, timeout, crash, and restart.

The crux question this design must answer:

> External payment already succeeded, but SpendShield crashes **before**
> `consume_once()` (or between consuming and booking). After restart, how do
> we guarantee the booking is eventually applied **exactly once**?

Answer is in §5. Short version: the durable store is the source of truth for
**outcome** (SUCCEEDED) and for **booking** (a durable ledger row); there is no
separate in-memory-only booking step in the intent path. At-most-once comes
from a single deduping ledger INSERT inside one SQLite transaction;
at-least-once comes from startup replay, which inserts any missing ledger row
for every SUCCEEDED intent. The "crashed between claim and book" window
cannot exist: there is no consumed marker apart from the booking row itself.

## 1. Scope & boundary

- SpendShield provides **durable authorization intent + once-only
  consumption semantics**. It does not orchestrate rails.
- Caller executes the rail with a persisted `provider_key`. Reconciler reports
  the outcome back. SpendShield records it durably and gates the local
  booking.
- v1 backend: SQLite (stdlib). Single logical estate (one active guard per
  store) assumed; multi-estate shared budget is an explicit extension (§8).
- No provider SDKs, no distributed transaction machinery.

## 2. Intent identity & fingerprint

```
scope      = (payer/agent, resource/recipient, idempotency_key)
fingerprint = {recipient, amount, currency, resource, payment_method}
```

- `create_or_get(agent, resource, idem_key, amount, fingerprint)`:
  - same scope + same fingerprint → **return existing intent** (idempotent)
  - same scope + different fingerprint → **IntentConflict**
  - different resource = different scope = independent intent
    (cv-scvd: never a wrong cached hit)
- `provider_key` generated once at RESERVED, persisted, **reused on retry**.

## 3. State machine & transition table

```
RESERVED ──► IN_FLIGHT ──► SUCCEEDED ──(consume_once)──► [booked in ledger]
                │
                └──► UNKNOWN ──reconcile──► SUCCEEDED
                              └──────────► RECONCILED_FAILED ──► (release)
```

| method | from | to | guard / CAS | note |
|---|---|---|---|---|
| `create_or_get` | — | RESERVED | UNIQUE(scope); fingerprint eq/conflict | idempotent create |
| `reserve(id)` | RESERVED | RESERVED | current-state check | claim "sender about to act" |
| `mark_in_flight(id)` | RESERVED | IN_FLIGHT | CAS `WHERE status='RESERVED'` | at most one sender |
| `mark_succeeded(id)` | IN_FLIGHT | SUCCEEDED | CAS | direct ack path |
| `mark_unknown(id)` | IN_FLIGHT | UNKNOWN | CAS | timeout / crash after send |
| `reconcile(id, outcome, proof)` | IN_FLIGHT/UNKNOWN | SUCCEEDED | CAS + proof required | proof = rail txn id / key |
| `reconcile(id, outcome, proof)` | IN_FLIGHT/UNKNOWN | RECONCILED_FAILED | CAS + proof required | proof = proven absent |
| `consume_once(id)` | SUCCEEDED | SUCCEEDED (+ ledger row) | INSERT dedup by booking PK (see §5) | exactly one True |

Illegal transitions raise (tests: `mark_succeeded` on UNKNOWN raises;
`mark_in_flight` on UNKNOWN / already IN_FLIGHT returns False).

## 4. UNKNOWN rules

- **UNKNOWN ≠ retry allowed.** An UNKNOWN intent blocks re-authorization of
  the same logical payment until (a) reconciliation proves the original
  outcome, or (b) an explicit expiry policy (v1: reserved extension) says
  retry is safe.
- The caller's retry loop **never** advances this state. Only `reconcile`
  with a proof does.
- Retry hitting an UNKNOWN intent returns the same intent; no second intent,
  no second external execution.

## 5. SUCCEEDED vs consume_once — two facts, and crash recovery

Two facts must stay separate (they are **not** one atomic transition):

1. **SUCCEEDED** = durable *external-outcome* fact: rail committed (proof
   recorded). Set by `mark_succeeded` (ack) or `reconcile` (recovery proof).
2. **consumed / booked** = durable *local-effect* fact, derived from a
   **booking ledger row** (see protocol below), not from a separate flag.

`consume_once(id)` = the single deduping INSERT that books the amount. Exactly
one caller in the history of an intent gets `True`; everyone else gets `False`.

### Booking protocol — revised (方案 A: booking ledger in the same durable commit)

**Correction (2026-09-04, Felix review):** the earlier draft said "claim CAS → guard.book() → COMMIT" inside one SQLite txn. That was wrong: `guard.book()` mutates the **in-memory estate** (`_spent`, V2 estate, audit, recipient memory) — it is not durable and does not participate in any SQLite transaction. Writing `consumed=1` to SQLite while the booking lives in memory would recreate the original crash window as "consumed → book", not close it.

Direct answers:
- What durable state does `guard.book()` modify? **None today.** The SpendShield budget estate is per-process in-memory and resets on restart.
- Can that state join the same SQLite transaction as `consume_once()`? **No — unless booking itself becomes a durable record in the store.**

Therefore v1 takes 方案 A (scoped to the intent path): the store owns a durable **booking ledger**, and booking for intent-path payments IS a ledger row committed in the same transaction as consumption. The separate `consumed` column is dropped — **consumed is derived: it exists iff a booking row exists** (one source of truth, no dual-state drift).

```
consume_once(intent_id):           # one durable commit boundary
  BEGIN IMMEDIATE
    SELECT status FROM intents WHERE id=?          -- must be SUCCEEDED, else return False
    INSERT INTO bookings (intent_id, agent, resource, amount, currency, created_at)
      VALUES (...)                                  -- PK(intent_id) dedups; rowcount=1 ⇒ winner
  COMMIT
  # winner (rowcount 1) → True; loser (conflict) → False
  # durable facts committed together: booking row ⇔ consumed ⇔ local effect
```

- `consume_once()` does **not** independently persist a consumed flag. The winning INSERT **is** the durable booking commit; the consumed fact is that row's existence.
- The in-memory estate cache (`guard._spent`, etc.) is updated **after** COMMIT as a cache, never as the source of truth.
- **Startup rebuild (fixes "undercount budget after restart")**: on guard startup with an attached store, every intent in SUCCEEDED state is run through `consume_once` — rows already present are no-ops (PK conflict), missing rows are inserted and the cache is incremented. The estate therefore converges to the durable ledger without double counting.

At-most-once: booking row PK + single txn admits one booker per intent, ever.
At-least-once: startup replay inserts any missing booking row for a SUCCEEDED intent.
Crash anywhere: no durable side effect until COMMIT; after COMMIT the row exists and estate cache rebuilds from it. The window "consumed but unbooked" cannot exist because there is no consumed marker apart from the booking row itself.

### Legacy path (explicitly out of the new contract)
Non-intent `guard.book()` calls (e.g. server-side `X402PaywallGuard.confirm_payment` today) stay in-memory as today — unchanged semantics, documented as not restart-durable. Only intent-path payments claim the durable exactly-once contract in v1.

## 6. Reservation & budget

- `reserve()` records the reserved amount on the intent row at create time
  (durable), so a retry cannot silently change what was reserved.
- Full **budget** (check → hold → release) stays owned by the SpendShield
  guard estate, not by the store. v1 coupling: intent creation + reservation
  are one SQLite txn; budget check/hold against the estate happens under the
  guard lock in the same call path (in-process atomicity).
- RECONCILED_FAILED → reservation releasable (only then, or pre-dispatch, or
  on trusted evidence of non-dispatch — per APS §3.5 lesson).

## 7. SQLite schema (reference backend)

```sql
CREATE TABLE intents (
  id            INTEGER PRIMARY KEY,
  agent         TEXT NOT NULL,
  resource      TEXT NOT NULL,          -- column: recipient (alias to/resource in API)
  idem_key      TEXT NOT NULL,
  amount        REAL NOT NULL,
  currency      TEXT DEFAULT 'USD',
  fingerprint   TEXT NOT NULL,          -- canonical JSON
  provider_key  TEXT NOT NULL UNIQUE,
  status        TEXT NOT NULL,          -- RESERVED|IN_FLIGHT|SUCCEEDED|UNKNOWN|RECONCILED_FAILED
  proof         TEXT,                   -- rail txn id / evidence
  active        INTEGER NOT NULL DEFAULT 1,  -- 0 = RECONCILED_FAILED, 释放 scope
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_intents_active_scope
  ON intents (agent, recipient, idem_key) WHERE active = 1;  -- partial: 终态失败不阻塞同 key 重试

CREATE TABLE bookings (                 -- durable booking ledger (方案 A)
  booking_id    TEXT NOT NULL UNIQUE,
  intent_id     INTEGER PRIMARY KEY REFERENCES intents(id),  -- one booking per intent
  agent         TEXT NOT NULL,
  recipient     TEXT NOT NULL,
  amount        REAL NOT NULL,
  currency      TEXT DEFAULT 'USD',
  created_at    TEXT NOT NULL
);
```

- Concurrency: `BEGIN IMMEDIATE` for write txns; the **partial** unique index on
  `(agent, recipient, idem_key) WHERE active=1` makes racing `create_or_get`
  converge on one row (winner commits, loser re-reads and returns the winner's
  intent — or raises Conflict on fingerprint mismatch), while a
  RECONCILED_FAILED intent (active=0) releases the scope so the same key can
  retry as a fresh intent (acceptance 2).
- `provider_key UNIQUE` doubles as a safety net.
- `consume_once` = one txn: `SELECT status` (must be SUCCEEDED) +
  `INSERT INTO bookings` (PK dedup = the once-gate). There is no consumed
  column to drift out of sync.

## 8. Out of scope for v1 (extensions)

- UNKNOWN expiry policy (auto-release after bounded time).
- Durable shared/multi-process budget estate **beyond the intent-path booking
  ledger** (the ledger makes intent-path bookings restart-durable; the full
  estate, holds, and legacy `book()` remain per-instance in v1).
- Multi-tenant store isolation, migrations, backends other than SQLite.
- APS authority-path/subtree reservation semantics (separate spike; the
  authority path becomes a *reservation scope input* later — not baked into
  this primitive).
- `_v2_replay` remains only as a process-local optimization cache; it no
  longer carries a security invariant.

## 9. Acceptance mapping (RED tests → this design)

| test | design element |
|---|---|
| ack lost → restart → replay ⇒ 1 exec + 1 booking | §2 provider_key reuse + §3 IN_FLIGHT CAS + §5 startup rebuild |
| timeout → reconcile absence → retry ⇒ 1 payment | §4 UNKNOWN + §3 reconcile→RECONCILED_FAILED→new intent |
| same key, same resource, diff amount ⇒ CONFLICT | §2 fingerprint |
| same key, diff resource ⇒ independent intent | §2 scope |
| two processes race same key ⇒ 1 intent / 1 exec | §7 UNIQUE + claim CAS |
