# 🏅 SpendShield Security Hall of Fame

> Honoring attackers who made an unauthorized transaction get ALLOW.
> Every entry below is a **real bypass** that became a permanent regression test — SpendShield is stronger because of them.

## Rules of engagement

- 🧪 **Sandbox only** — `dry_run=True`, test keys, never real payment systems.
- 📝 Open an issue with a minimal reproduction (agent, amount, to, policy, expected vs actual).
- 🏅 First valid bypass per attack class gets credited here.
- 🔒 Valid = the bypass is reproducible, violates a documented invariant, and gets fixed + regression-tested.

## Current status

- **234 tests** · 14 security suites · **0 known escapes**

## Honored

| Date | Attacker | Attack class | Finding | Fixed in | Regression test |
|---|---|---|---|---|---|
| — | *waiting for the first one* | | | | |

## Known attack classes already covered (already defended)

| Class | Defense | Test suite |
|---|---|---|
| Budget bypass (concurrent) | locked evaluate-and-book | `tests/security/test_budget_bypass.py` |
| Race condition (approve) | single-flight approval | `tests/security/test_race_condition.py` |
| Replay (same key) | idempotency keys | `tests/security/test_replay_attack.py` |
| Double spend (approve twice) | pending consumed once | `tests/security/test_double_spend.py` |
| Policy bypass (NaN, empty, tampering) | input gates + policy fingerprint | `tests/security/test_policy_bypass.py` |
| Approval bypass (tampered amount) | re-evaluate on approve | `tests/security/test_approval_bypass.py` |
| Parameter tampering (domain spoof) | exact-domain matching | `tests/security/test_parameter_tampering.py` |
| Credential leak (meta in audit) | redaction | `tests/security/test_credential_leak.py` |
| Stale approval after policy change | pending cleared on reload | `tests/security/test_security_audit.py` |
| MCP tool-chain abuse | host-level audit | `tests/security/test_hardening_mcp_adv.py` |
| Simulator ≠ real divergence | differential testing | `tests/security/test_hardening_sim_consistency.py` |
| State machine jumps | lifecycle state machine | `tests/test_policy_lifecycle.py` |
| Audit tampering | hash chain | `tests/test_audit_trail.py` |

---

*The gate is only as strong as the attackers it has survived. Break it — we'll make it stronger.*
