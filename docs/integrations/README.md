# SpendShield Integration Cookbook

> 每个生态一页, 固定结构: **Problem → Architecture → 20-line integration → Enforcement → Proof → Limitations**。
> 规则: 只收录有真实代码/证据的集成(不写臆想集成); 每接一个生态加一页 → 最终 = SpendShield Integration Map。

| Entry | Ecosystem | 形态 | 状态 |
|---|---|---|---|
| [agentkit.md](agentkit.md) | Coinbase AgentKit | 转账 action 前过授权层(wrapper gate) | ✅ working example + [issue #1484](https://github.com/coinbase/agentkit/issues/1484) |
| [x402.md](x402.md) | x402 (Coinbase) | client 侧 protect_x402_payment / server 侧 paywall guard | ✅ adapter + three-channel demo |
| ap2.md | Google AP2 | authority vectors / conformance 对话 | 🔜 有 conformance 资产([docs/conformance/ap2-authority-vectors.md](../conformance/ap2-authority-vectors.md)), 等真集成形态 |
| mpp.md | MPP / solana pay CLI | 跨轨对话 | 🔜 有联系(#issue 轨道), 无代码集成 |
| stripe.md | Stripe | three-channel demo 同策略零改动 | 🟡 仅 mock 演示, 非真集成(诚实标注) |

## 相关资产地图(本 cookbook 的上游/下游)
- 概念: [concepts 簇](https://felixpg13-glitch.github.io/spendshield/ai-agent-payment-authorization/) · [examples/integration/](../../examples/integration/)(可跑代码)
- 证据: [Ecosystem Matrix](../ecosystem-matrix.md) · [Failure Modes](../failure-modes.md) · [Attack Corpus](../attack-corpus.md) · [Conformance](../conformance/ap2-authority-vectors.md) · [composition](../../composition/)
