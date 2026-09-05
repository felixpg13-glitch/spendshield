# SpendShield × AgentKit — a working integration example

**Policy authorization for AgentKit payment actions — evaluate spending policy
before invoking a wallet action.**

```text
ALLOW   → AgentKit action invoked (exactly once)
APPROVAL → human approves → invoked once
DENY    → action never invoked
REPLAY  → grant refused → action never invoked
```

> ⚠️ This is a **working integration example / prototype** — not an official
> Coinbase integration. **No changes to AgentKit are required**; it uses only
> the public AgentKit SDK surface (`WalletActionProvider`, the `WalletProvider`
> interface, and real `Action.invoke` calls).

## Why this example exists

AgentKit payment actions can execute transfers and x402 payments, but applications
may want an additional policy layer — budget, per-transaction caps, merchant
allow/block lists, human approval — *before* invoking those actions. SpendShield is
that layer: it decides, AgentKit executes.

```text
Agent decides to transfer
        │
        ▼
SpendShield.authorize(agent, amount, recipient)      ← policy: budget / cap / merchant list / approval
        │
        ├── ALLOW    → signed one-time grant issued
        ├── APPROVAL → human approves → grant issued
        └── DENY     → AgentKit payment action is NEVER invoked
        │
        ▼
Executor.verify(grant)   ← consumed once; replay of the same grant → REFUSED
        │
        ▼
AgentKit Action.invoke() → WalletProvider.native_transfer()   ← the only execution path
```

## What it proves (with counters, not vibes)

The demo counts real `WalletProvider.native_transfer` calls — the true execution
point under `Action.invoke` — and asserts on the counts:

| Scenario | SpendShield | native_transfer executions |
|---|---|---|
| $5 to trusted merchant | ALLOW | 1 |
| $50 (over $30 autonomous limit) | APPROVAL → human grant | 1 |
| same grant replayed | REUSED → REFUSED | 0 additional |
| $500 to blocked address | DENY (merchant blocked) | **0** |

A DENY (or replay) that still executed would FAIL the assertions.

### Why a deterministic wallet provider?

The demo uses a deterministic `WalletProvider` implementation (no chain, no keys)
**to make the execution boundary observable and reproducible**. The integration
path still calls AgentKit's real `Action.invoke` and `WalletProvider.native_transfer`
— the same code path a real `EthAccountWalletProvider` / CDP wallet would run.
Swap in your real wallet provider and the gate stays exactly the same.

## Run it (one command)

```bash
pip install -r requirements.txt
python run_demo.py
```

No chain, no real money, no configuration. Policy amounts are symbolic units.

## The gate (the whole integration boundary, ~20 lines)

```python
res = shield.authorize(agent=AGENT, amount=amount, to=to)      # 1. policy decision
if res.decision == "DENY": return  # AgentKit never invoked
if res.decision == "APPROVAL":     # 2. human (here: auto-granted)
    res = shield.approve(res.approval_id, by="human-demo")
grant = issuer.issue(...)                                       # 3. sign one-time grant
ok, _ = executor.verify(grant, ...)                             # 4. consume it (fail-closed)
if ok:
    action.invoke({"to": to, "value": str(amount)})             # 5. only now: AgentKit executes
```

Everything before step 5 — budget, per-transaction cap, merchant block list, human
approval line, replay protection — comes from `policy.yaml`, not application code.

## Files

- `run_demo.py` — the demo + assertions (exit 0 = all pass)
- `policy.yaml` — policy: daily $100 budget, $50 per-tx cap, trusted/blocked
  recipient addresses, $30 approval threshold for `demo-agent`
- `requirements.txt` — pinned dependency versions

## Expected output

```text
Scenario 1: $5 transfer to trusted merchant
  SpendShield -> ALLOW -> AgentKit EXECUTED
  wallet executions: 1

Scenario 2: $50 transfer (over $30 autonomous limit)
  SpendShield -> APPROVAL -> human approved -> SpendShield -> ALLOW -> AgentKit EXECUTED
  wallet executions: 2
  replay same grant -> REFUSED (REUSED)
  wallet executions: 2

Scenario 3: $500 transfer to blocked address
  SpendShield -> DENY (merchant '0x9999...' is blocked)
  wallet executions: 2

Assertions (execution boundary):
  [PASS] ALLOW  -> AgentKit executed exactly 1 time
  [PASS] APPROVAL -> human grant -> executed exactly once
  [PASS] REUSED grant -> 0 additional executions
  [PASS] DENY (blocked merchant) -> AgentKit NOT invoked

ALL ASSERTIONS PASSED ✅
```
