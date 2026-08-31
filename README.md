# 💰 SpendShield — Payment Guardrails for AI Agents

> **Before your AI spends real money, it passes through SpendShield.**

An open-source payment safety layer for Python and MCP. Give your AI agent a **spend-capped digital identity (KYA)**, run every payment through four deterministic gates, defend against prompt injection, and keep secrets in an encrypted vault.

## 🩸 Why this project exists (a real incident)

On August 9, 2026, my automation system ran a test order. I sent `dry: true`, expecting a price preview. The server only honored `?dry=1` — **4 orders of ¥99 were charged for real, and the money was gone.**

This is not just my problem. AI agents are about to order food, top up accounts, and call paid APIs on your behalf. **When AI starts spending real money, who puts a gate in front of it?**

I turned my scar into a library.

## ✨ Three trust pillars

| Pillar | What it does |
|---|---|
| 🔑 **Identity (KYA)** | Every agent gets a digital identity with its own budget/blacklist/limits. **Unregistered agents are denied by default.** |
| 🎯 **Intent alignment** | New recipients and large amounts **always require human sign-off** — stops prompt-injected agents from spending without you. |
| 🔐 **Secret vault** | Keys encrypted at rest (AES-256), master key never on disk. Key access passes the gates and is **fully audited**. |

## 🚧 Four deterministic gates

Every spend passes all of them. Rules are code, not AI opinion — agents cannot argue, trick, or inject their way past.

| Gate | Default | Effect |
|---|---|---|
| 🧪 **dry_run** | On | Preview only. Nothing executes until you say so. |
| 💰 **budget** | Unlimited | Hard ceiling. Over budget means denied. |
| 🚧 **max_amount** | Unlimited | Per-transaction cap. |
| 🙋 **approval** | Off | Human sign-off — console, Telegram, or webhook. |
| 📜 **audit** | On | Every attempt recorded, exportable JSON. |

## 🚀 Quick start

```bash
pip install spendshield
```

```python
from spendshield import SpendShield, KeyVault

guard = SpendShield(budget=200, dry_run=True, whitelist=["McDonald's"])

@guard.protect("order")
def place_order(amount, to):
    return call_real_api(amount, to)

place_order(amount=99, to="McDonald's")
# => DryRunBlocked: dry_run mode, nothing executed

guard.dry_run = False
for i in range(4):
    place_order(amount=99, to="McDonald's")   # 3rd order blocked by BudgetExceeded
```

### Agent identity (KYA)

```python
guard = SpendShield(dry_run=False)
guard.register_agent("mcd_bot", budget=50, max_amount=30,
                     blacklist=["unknown_vendor"], whitelist=["McDonald's"],
                     rate_limit={"window_s": 60, "max_calls": 3})

@guard.protect("order", agent="mcd_bot")
def place_order(amount, to):
    return call_real_api(amount, to)
```

### Secret vault

```bash
python -c "from spendshield import KeyVault; print(KeyVault.generate_key())"
export SPENDGUARD_MASTER_KEY=***   # never commit this
```

```python
vault = KeyVault("vault.json")
vault.store("mcd_sk", "sk_live_xxx")

guard = SpendShield(key_vault=vault)
guard.register_agent("mcd_bot", whitelist=["mcd_sk"])
sk = guard.get_secret("mcd_sk", agent="mcd_bot")   # passes identity + intent gates
```

## 🤖 MCP Server

Claude Code, OpenClaw and any MCP-compatible agent can call the guard directly:

```bash
spendshield-mcp --policy spendshield.yaml
```

Tools: `spend_protect` / `spend_status` / `spend_audit` / `spend_reset` / `secret_get`

## 🧪 Tests

30 tests covering gates, identity, intent alignment, vault, and edge cases.

```bash
python3 -m pytest tests/
```

## 📝 Feedback & Contributing

- 🐛 Found a bug? [Open an issue](https://github.com/felixpg13-glitch/spendshield/issues/new?template=bug_report.md)
- 💡 Have an idea? [Suggest a feature](https://github.com/felixpg13-glitch/spendshield/issues/new?template=feature_request.md)
- 🔒 Security vulnerability? See [SECURITY.md](SECURITY.md) — report privately, not in a public issue.
- ⭐ Found it useful? Star the repo so other people who got burned by "test orders" find it.

## 📄 License

MIT — take it. May no one get burned by a "test order" twice in the AI era.
