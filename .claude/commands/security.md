---
description: Run security/adversarial suites
---

Run the security-focused suites (budget bypass, replay, double-spend, invariants, enforcement/provenance, adversarial):

```bash
python3 -m pytest tests/security/ tests/test_enforcement.py -q
```

Report pass counts. Security invariants must never break — a security test failure is a P0, stop feature work and fix it first.
