# VA × SpendShield E2E composition

**Status (2026-09-03, honest version):**
Core pre-dispatch handler-gating behavior has been **independently reproduced** by the
Verb Authority maintainer from clean checkouts (SpendShield `83f2a415…`, VA `5ef6e110…`).
The evaluator then identified three composition-evidence gaps (artifact integrity,
canonical source-binding, incomplete grant lifecycle). All three were corrected and
re-pinned at `0acb2e5`; **full VA → SpendShield composition evidence is not yet frozen —
it now includes the evaluator's final load-bearing requirement (source-bound negative probes). Awaiting clean-checkout re-confirmation before recording as frozen.**

This is not a self-test claim. It is an external adversarial review cycle in progress:
independent reproduction → evidence-gap findings → correction → pending re-confirmation.

## Files
- `va_e2e_authorize_payment.py` — the 7-case composition test (exit != 0 on any failure)
- `policy.va_e2e.yaml` — dedicated policy isolating the composition chain from budget mechanics
- `va_e2e_results.txt` — frozen stdout of the corrected run (7/7 PASS, exit 0)

## Run
```bash
PYTHONPATH=<verb-authority source> python3 composition/va_e2e_authorize_payment.py
```

## Pins
- Host: felixpg13-glitch/spendshield @ `0acb2e5`
- Verb Authority: main @ `5ef6e1109120` (2026-09-03)
- External review thread: yairsabag/verb-authority issue #7
