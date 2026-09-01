---
name: Pull request
about: Contribute to SpendShield
title: ''
labels: ''
assignees: ''
---

## What & why

<!-- What does this change, and what real problem does it solve? -->

## 5 questions (see CONTRIBUTING.md)

1. **Capability added:** 
2. **New attack surface:** 
3. **New invariants:** 
4. **Regression cases added:** 
5. **Old tests still pass:** `python3 -m pytest tests/ -q` → 

## Security check

<!-- Has this change given an attacker a new way to spend money? If unsure, don't merge. -->

## Tests

- [ ] Full suite: `python3 -m pytest tests/ -q`
- [ ] Security suites: `python3 -m pytest tests/security/ -q`
- [ ] Regression tests added for any bug fixed
