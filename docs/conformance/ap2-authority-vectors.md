# AP2 Authority Vectors — minimal shared set

> Status: working draft (2026-09-03) · Origin: google-agentic-commerce/AP2 discussion
> [#332](https://github.com/google-agentic-commerce/AP2/discussions/332) and #211 —
> surfaced from implementation experience with an agent-spending enforcement layer.
>
> Scope: **AP2-native and implementation-neutral.** Vectors express mandate input,
> derived checkout/execution input, and an expected verifier status/question only.
> No dependency on Quesen, SpendShield, or any enforcement architecture.
>
> Conventions:
> - "closed mandate": authorization bound to a specific checkout (`checkout_hash`).
> - "open mandate": authorization over declared constraint dimensions
>   (amount, currency, merchant, recurrence, delivery window, …).
> - "derived checkout": a checkout produced under an open mandate.
> - A dimension the mandate **never names** is *authority-underspecified*.

## Status legend
| Status | Meaning |
|---|---|
| **established** | expected verifier behavior is unambiguous |
| **normative question** | expected behavior depends on an AP2 semantic not yet specified |

---

## C1 — closed → execution, checkout differs → integrity failure (established)

- **Mandate (closed):** binds `checkout_hash = H(checkout_A)`
- **Execution input:** checkout_B, where `H(checkout_B) ≠ H(checkout_A)`
- **Expected:** integrity failure — verifier MUST reject; a prior authorization does
  not bind to a different checkout. Any serialized mutation voids the closed binding.
- **Notes:** pure recomputation; no policy needed; independently verifiable.

## C2 — open → closed, declared constraint true → valid derivation (established)

- **Mandate (open):** `amount ≤ 100`, `currency = USD`, `merchant ∈ {A, B}`
- **Derived checkout:** `amount = 80`, `currency = USD`, `merchant = A`
- **Expected:** derivation remains within the declared authority — verifier MAY pass
  (all other gates permitting).
- **Notes:** constraint evaluated per dimension against the derived checkout.

## C3 — open → closed, declared constraint false → authority violation (established)

- **Mandate (open):** `amount ≤ 100`, `currency = USD`, `merchant ∈ {A, B}`
- **Derived checkout:** `amount = 150` (or `merchant = C`, `currency = EUR`)
- **Expected:** authority violation — verifier MUST reject on the failing dimension.
- **Notes:** a dimension outside every declared constraint is not "authorized" merely
  because it is unconstrained *by that constraint*; see C4 for the naming case.

## C4 — open → closed, unnamed authority-bearing dimension → normative question (open)

- **Mandate (open):** `amount ≤ 100` — `merchant` is **never named**
- **Derived checkout:** `amount = 80`, `merchant = X`
- **Expected:** **unresolved.** What MUST a conforming AP2 verifier do when the derived
  checkout contains an authority-bearing dimension the open mandate does not express?
  This vector intentionally records the *question*, not an answer.
- **Candidate semantics once AP2 specifies omission:**
  - *not delegated* → C4 must not pass without renewed authorization;
  - *unconstrained* → C4 passes on this dimension;
  - *delegated to semantics elsewhere* → verdict comes from that context, not this mandate.
- **Status:** normative question — forks into ordinary pass/fail cases when the
  protocol resolves the omission semantic. No change to C1–C3.

---

## Suggested use
- Reference from #211/#332 as a shared, executable problem surface.
- C1–C3 are already convertible to pass/fail conformance tests.
- C4 stays an ambiguity vector until AP2 specifies the omission semantic; at that
  point it splits into the corresponding pass/fail cases unchanged otherwise.

## Provenance note
This set was first written down by the SpendShield project as a neutral artifact of
the AP2 discussion. The C4 case originates from implementation experience: an
enforcement engine that treats unnamed authority-bearing dimensions as fail-closed
by *operator policy* — deliberately not as a claim about what AP2 should mandate.
