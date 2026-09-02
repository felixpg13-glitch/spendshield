# Competitive Position (2026-09-02)

> 决策记录: SpendShield 的边界 = **policy decision 层**。不吞上游 authority, 不吞下游 execution/security。

## 分层地图

```
Identity / Delegation (agent-passport 方向)   ← 我们不建, 接口兼容
        │  authority proof
        ▼
SpendShield — Policy control plane            ← 我们在这里
        │  budget / txn limits / merchant / approval / simulation / versioning / rollback / audit
        ▼
Execution safety (SafeAgent 方向: idempotency) ← 我们不建, 接口兼容
        ▼
Payment security middleware (presidio 方向)    ← 我们不建, 接口兼容
        ▼
Payment rail (x402 / Stripe / orders / wallets)
```

## 威胁排序(2026-09-02 调研)

| 项目 | 短期 | 长期 | 关系 |
|---|---|---|---|
| presidio-hardened-x402 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 工程最深的直接竞争者(payment security pipeline, 有论文/评估) — 月度跟踪 |
| agent-passport-system | ⭐⭐ | ⭐⭐⭐⭐⭐ | 协议层潜在替代(authority ≠ policy) — 尽早研究兼容 |
| SafeAgent | ⭐ | ⭐⭐ | 下游互补(execution correctness) |

**真威胁**: 轨道方(Stripe/OpenAI ACP/AWS AgentCore/Visa)把基础 policy 做成默认功能 → 独立层的存在理由 = 跨轨道治理深度 + 真实工作流嵌入。

## 我们不做的(防 feature race)
- PII 检测/脱敏、Prometheus/K8s 全家桶、NLP — presidio 的战场
- 加密委托/身份/声誉/密钥基建 — agent-passport 的战场
- execution 幂等/重试状态机 — SafeAgent 的战场

## 接口留白(设计上兼容, 不真集成 0 用户项目)
- authorize 入参可携带 authority context(opaque)
- 输出 signed/auditable decision record(已有 decision_id + policy_version + audit chain)
- 幂等键/replay 防护(已有)

## 决策记录格式(下一步契约升级)
Authority context + Payment intent + Policy → ALLOW / DENY / REQUIRE_APPROVAL → signed decision

## 战略锁定(2026-09-02 16:54, Felix 确认)
- **一句话定位**: Should this spend happen right now, under the current policy?(APS = Who has the authority? 我们 ≠)
- **不因 APS 改路线**: 不补 identity/passport/delegation/reputation/A2A/多语言 SDK — 那会把我们拖成协议项目
- **差异化做成可证明**: Policy lifecycle 治理(谁改的/上线前模拟过吗/怎么 rollback/这笔按哪个版本执行)——已建成, 待做成 60 秒可见的 demo
- **Phase A(本周, 最高优先级, 不加功能)**: ①1 个陌生开发者反馈(HN 捞) ②1 次外部 integration attempt(APS issue) ③麦当当 TEST_MODE dogfood(reference case)
- **Phase B**: 最小接口 `decision = authorize(intent, context)` → ALLOW/DENY/REQUIRE_APPROVAL, 不关心钱怎么动
- **Phase C**: Adapter 结构(Core + MCP/APS/x402 adapters), 不吞协议
