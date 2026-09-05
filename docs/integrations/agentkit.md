# SpendShield × Coinbase AgentKit

## Problem
AgentKit payment actions (native_transfer, x402, erc20) can execute payments, but applications may
want an additional policy layer before invoking them. AgentKit has no policy hook in its action loop.

## Architecture
```text
agent decides to transfer
  → SpendShield.authorize()        (budget / cap / merchant list / approval line)
  → ALLOW: signed one-time grant
  → executor.verify(grant)         (consumed once; replay refused)
  → AgentKit Action.invoke() → WalletProvider.native_transfer()
```

## 20-line integration
```python
res = shield.authorize(agent=AGENT, amount=amount, to=to)
if res.decision == "DENY": return                      # AgentKit never invoked
if res.decision == "APPROVAL":
    res = shield.approve(res.approval_id, by="human")  # re-checked vs live policy
grant = issuer.issue(agent=AGENT, amount=amount, currency="ETH",
                     merchant=to, policy_version=res.policy_version)
ok, _ = executor.verify(grant, agent=AGENT, amount=amount,
                        currency="ETH", merchant=to)   # consume once
if ok:
    action.invoke({"to": to, "value": str(amount)})    # only now: AgentKit executes
```

## Enforcement
- Execution is counted at `WalletProvider.native_transfer` (real execution point under `Action.invoke`).
- DENY or grant replay → zero invocations (asserted).

## Proof
`examples/integration/agentkit/run_demo.py` — 4/4 assertions pass, exit 0:
$25 trusted → ALLOW · executed 1 · $50 over-line → APPROVAL→human→1 · replay → REFUSED · 0 · $500 blocked → DENY · 0.
Discussion: [coinbase/agentkit#1484](https://github.com/coinbase/agentkit/issues/1484).

## Limitations
- AgentKit core has no middleware hook; the gate wraps the action boundary (example-level integration).
- Demo uses a deterministic wallet provider (no real chain); swapping in a real wallet keeps the gate identical.
- coinbase-agentkit 0.7.x imports `solana.rpc.api` (removed in solana ≥0.37) — demo stubs it; upstream fix pending.
