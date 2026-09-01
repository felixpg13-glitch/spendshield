# Reddit r/Python 帖(文字帖带链接, 避免纯广告被 filter)

**Title:**
My automation spent $15 on a "test order" (dry flag silently ignored), so I built an open-source payment guardrail for AI agents

**Body:**

Long story short: I sent `dry: true` in the request body, the server only honored `?dry=1` in the URL, and 4 x ¥99 got charged for real. Test order, real money.

That got me thinking — AI agents are about to order food, buy compute, call paid APIs. Every one of those is a new place where a dry-run flag can silently fail, a budget can be exceeded, or a prompt-injected agent spends without permission.

So I built [SpendShield](https://github.com/felixpg13-glitch/spendshield): a Python + MCP payment guardrail with:

- **Spend-capped agent identity (KYA)** — every agent registers its own budget/blacklist; unregistered = denied
- **Four deterministic gates** — dry-run, budget, amount limit, human approval (console/TG/webhook)
- **Intent alignment** — new recipients and large amounts always require human sign-off (prompt-injection defense)
- **Encrypted secret vault** — AES-256 at rest, master key never on disk, access audited
- **x402 adapter** — gates payments on the emerging HTTP-402 protocol before settlement

```python
guard = SpendShield(budget=200, dry_run=True, whitelist=["McDonald's"])

@guard.protect("order")
def place_order(amount, to):
    return call_api(amount, to)

place_order(amount=99, to="McDonald's")  # DryRunBlocked, nothing executes
```

36 tests, MIT, `pip install spendshield`.

Would love feedback from anyone who's built agents that touch money — what am I missing? What would make this actually useful for you?

⭐ https://github.com/felixpg13-glitch/spendshield
