---
name: 🏴 Security / Bypass finding
about: You found a way to make an unauthorized transaction get ALLOW (see Break the Gate challenge)
title: '[SECURITY] '
labels: security
assignees: ''
---

**⚠️ Public disclosure policy**
If this is a **live vulnerability** that could affect production users, **do NOT post details publicly**. Contact us privately first (see [SECURITY.md](../../SECURITY.md)).

If you're running the **Break the Gate challenge** (sandbox only, `dry_run=True`), public issues are welcome:

**Attack description**
What did you do, and what should have happened instead?

**Minimal reproduction**
```python
# policy, agent, amount, to — everything needed to reproduce
```

**Invariant violated**
Which documented invariant did you break? (e.g. over budget → no payment)

**Environment**
- Python version:
- spendshield version:

You'll be credited in the [Security Hall of Fame](../../SECURITY.md) if it's a valid first-of-class finding.
