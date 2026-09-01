# Contributing to SpendShield

Thanks for considering contributing! SpendShield guards real money, so we take correctness and security seriously.

## What we welcome

- **Bug reports** — with a minimal reproduction
- **Security findings** — please read [SECURITY.md](SECURITY.md) first (responsible disclosure, Hall of Fame)
- **Attack attempts** — this is a security project: try to break the gate, open an issue with a reproduction
- **Documentation improvements** — README, docs/, examples
- **Policy examples** — real-world spending policies you've written

## What we probably won't accept (yet)

- **New features without a real user story** — SpendShield is in validation phase. Before adding a feature, answer: *who needs this, and what real problem does it solve?* Unprompted enterprise features get closed.
- **Refactors of working code** — surgical changes only.

## Development setup

```bash
pip install -e .[dev]
python3 -m pytest tests/        # all tests must pass
```

## Before opening a PR

1. Run the full test suite: `python3 -m pytest tests/ -q` (currently 240 passing)
2. Add regression tests for any bug you fix — **every discovered hole becomes a permanent test**
3. Run the security suites: `python3 -m pytest tests/security/ -q`
4. Ask the 5 questions:
   - What capability does this add?
   - What new attack surface does it create?
   - What new invariants does it add?
   - How many regression cases?
   - Do all old tests still pass?

## Security constitution

The 8 invariants in [SECURITY.md](SECURITY.md) must never break. V3+ layers must not violate them. Any P0/P1 security bug blocks release.

## License

By contributing you agree your work is licensed under MIT (see [LICENSE](LICENSE)).
