# VA × SpendShield E2E composition

**Status (2026-09-04, recorded):**
**Assisted external composition evidence — narrow GO from the Verb Authority maintainer.**
He independently reproduced the corrected chain from clean checkouts (SpendShield
`31e584b`, VA `5ef6e110…`): 7/7 cases, exit 0, stdout matched byte for byte, and
ran the merchant source variants directly against the pinned verifier. This commit
adds those merchant variants to the committed harness as probes D/E/F.

Claim boundary (maintainer's own framing, agreed): this is a purpose-built,
co-designed harness — **not** self-service adoption, independent certification,
production payment execution, or proof of truthful origin. It demonstrates that
host-owned `trusted_args` can stop protected-value changes before the SpendShield
handler, while an allowed call reaches a real SpendShield ALLOW and signed-grant
verification.

## Files
- `va_e2e_authorize_payment.py` — the 7-case composition test (exit != 0 on any failure)
- `policy.va_e2e.yaml` — dedicated policy isolating the composition chain from budget mechanics
- `va_e2e_results.txt` — frozen stdout of the corrected run (7/7 PASS, exit 0)

## Run
```bash
PYTHONPATH=<verb-authority source> python3 composition/va_e2e_authorize_payment.py
```

Built-in source checks: **two negative probes (A: agent omitted → SOURCE_MISSING:agent;
B: amount replaced → SOURCE_MISMATCH:amount) + one positive control (C: complete map →
AUTHORIZED)**, plus merchant supplemental probes D (merchant omitted at issuance →
SOURCE_MISSING:merchant), E (merchant replaced → SOURCE_MISMATCH:merchant), F (verifier
omits merchant → SOURCE_MISSING:merchant). `va_e2e_results.txt` is the frozen stdout.

## Pins
- Host: felixpg13-glitch/spendshield — composition chain `0acb2e5` → probes `31e584b`
  → merchant probes (this commit); external clean-checkout reproduction pinned `31e584b`
- Verb Authority: main @ `5ef6e1109120` (2026-09-03)
- External review thread: yairsabag/verb-authority issue #7 (GO) · write-up: issue #36
