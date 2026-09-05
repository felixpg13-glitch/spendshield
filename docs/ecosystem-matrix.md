# Ecosystem Compatibility Matrix(2026-09-05)

> 每个生态一行, 生态更新时只改这一行。AI 搜索偏好结构化比较(「x402 vs AP2 vs MPP authorization」)→ SpendShield 自然进入答案。
> 列含义: 该生态/项目是否提供 authorization 决策 / policy 规则 / spending limits / 与 SpendShield 的接口形态。诚实标注, 不写臆造能力。

| Ecosystem | Authorization decision | Policy rules | Spend limits | SpendShield 接口 | 证据 |
|---|---|---|---|---|---|
| **x402** (Coinbase) | 协议级单笔授权(signed payment) | ❌ 协议外 | ❌(client/wallet 层自理) | ✅ adapter(客户端 + paywall 两侧) | [x402.md](integrations/x402.md) |
| **AgentKit** (Coinbase) | 无(执行层只管签) | ❌ | 🟡 CDP 托管钱包服务端有 | ✅ example(wrapper gate) | [agentkit.md](integrations/agentkit.md) + [#1484](https://github.com/coinbase/agentkit/issues/1484) |
| **AP2** (Google) | 意图层(authority vectors) | 🟡 方向一致 | 🟡 ceiling/delegation 语义 | 🟡 conformance 资产已建, 无代码适配 | [conformance/ap2-authority-vectors](../conformance/ap2-authority-vectors.md) |
| **MPP / solana pay CLI** | user-authorized signing(单签) | ❌ | ❌ | 🟡 联系中, 无代码 | — |
| **Stripe** | ❌ | ❌ | 🟡 平台侧有 | 🟡 仅 three-channel mock 演示 | examples/three_channel_demo.py |
| **Omniclaw** | 🟡 guard 链(无签名票据) | ✅ policy.json | ✅ | ❌ 无插槽(决策+托管+执行一体) | Verdict: MONITOR |
| **Bindu** | 收款侧 x402 授权 | ❌(无支出侧) | ❌ | ❌ 无支出插槽(叙事互补) | Verdict: DISCUSS(轻) |
| **OpenAI/通用 agent** | — | — | — | ✅ MCP server / 库 | connect.html |

## 阅读法
- SpendShield = 跨轨 authorization 层: 决策不绑任何轨道, 各轨只提供「执行前的插槽」。
- 「✅ SpendShield 接口」列 = 有真代码/真证据; 其余诚实标 🟡/❌。
