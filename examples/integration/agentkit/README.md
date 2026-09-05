# SpendShield × AgentKit — transfer authorization example

**The integration shape:** SpendShield sits between the agent's decision and AgentKit's
payment execution. AgentKit has no policy hook in its action loop, so the gate wraps the
action boundary — the same place a LangChain / OpenAI-agents chatbot calls its tools.

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

## What it proves

Not "if allowed then transfer" — the full authorization semantics:

| Scenario | SpendShield | AgentKit execution |
|---|---|---|
| $5 to trusted merchant | ALLOW | executed exactly once |
| $50 (over $30 autonomous limit) | APPROVAL → human grant | executed exactly once |
| same grant replayed | REUSED → REFUSED | 0 additional executions |
| $500 to blocked address | DENY (merchant blocked) | **never invoked** |

The script counts real `WalletProvider.native_transfer` calls (the true execution point
under `Action.invoke`) and asserts on the counts — so a DENY that still executes would FAIL.

## Run it

```bash
pip install coinbase-agentkit spendshield
python examples/integration/agentkit/run_demo.py
```

No chain, no real money — the wallet provider is a deterministic stand-in and the policy
amounts are symbolic units.

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
approval line, replay protection — comes from `policy.yaml`, not from application code.

## Files

- `run_demo.py` — the demo + assertions (exit 0 = all pass)
- `policy.yaml` — the policy: daily $100 budget, $50 per-tx cap, trusted/blocked
  recipient addresses, $30 approval threshold for `demo-agent`

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
