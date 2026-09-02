# Security Hardening Backlog (Grok 反馈分诊, 2026-09-02)

> 原则: 发现一个洞 → exploit → 修复 → 永久 regression → 永不复发。
> 阶段纪律: Phase A(1 陌生用户反馈 / 1 integration attempt / 麦当当 dogfood)完成前**不动代码**。
> 本清单已按「已覆盖 / 真缺口」标注(2026-09-02 代码审计)。

## 核心架构判断(Grok, 认同)
`authorize()` 当前 = **可调用的安全检查(advisory)**, 不是**强制授权边界(enforced)**——
除非 Agent 没有第二条支付路径(唯一入口是架构事实, 不是库保证)。
升级方向: authorize() 签发**一次性授权凭证** → 支付执行方 verify() → 才执行。

## P0 — 已覆盖(别再建, 补测试即可)
| 项 | 现状 | 证据 |
|---|---|---|
| 并发预算原子性 | ✅ 有 | guard.py L139 `_v2_lock RLock`(评估-记账原子性, 防 TOCTOU) + 并发测试(mcp_edge_audit 并发100/预算锁) |
| 重放防护 | ✅ 有 | guard.py L140 `_v2_replay` + idempotency_key(同 key 已执行→DENY) + test_double_spend.py |
| Approval 绑定到原请求 | ✅ 基本有 | approve() 从 pending[approval_id] 取回完整 req(agent/amount/to 继承); approval_id 不可枚举、策略变更后旧 id 失效、重复 approve 只记一次账 |
| 未授权交易不可 ALLOW | ✅ 有 | 11,351 对抗套件 + 8 不变量 |

## P0 — 真缺口(Phase A 后第一轮)
1. **执行层强制(authorization enforcement)**: authorize 签发 one-time authz token, 绑定 {request_id, merchant, amount, currency, policy_version, expiry, nonce}; 支付路径必须 verify(authz) 才能执行 → 从 policy engine 升级为 authorization enforcement layer(与 APS 组合点)
2. **Approval→执行的显式回归测试**: 批准 $25/mcdonalds 的授权, 不能被用于 $250/amazon 的执行(当前靠架构无 token 可复用, 需测试钉死)
3. **设计注记**: enforcement 模式增加接入摩擦 → 未来做双模式(advisory 默认保 60 秒故事, enforcement 可选), 属 Phase B/C

## P1(Phase A 后, 不打断)
- Durable audit: 内存 hash chain → SQLite append-only(声称 production-grade 前)
- Merchant/recipient binding: display name ≠ 实际收款方(payment target verification, 未来亮点功能)
- Fail-closed recovery / credential isolation

## ✅ P0-1 原型已完成(2026-09-02 18:40, 1h 内)
- spendshield/enforce.py: AuthorizationIssuer(HMAC-SHA256 签发一次性 token) + Executor(verify, fail-closed)
- tests/test_enforcement.py 10 测试全过: 无token拒/篡改 amount·merchant·currency·policy_version 拒/过期拒/重放拒/伪造签名拒/错secret拒/happy path
- 设计: 字段绑定(签名覆盖 agent/amount/currency/merchant/policy_version/exp/nonce); 一次性(内存 used 集, 生产落盘)
- 未动 guard.py advisory 层(双模式: advisory 默认, enforcement 可选)
- 下一步: 生产化(持久化 used 集/密钥管理/接入麦当当 dogfood 验证 no-authz-no-money)
- 18:50 补充: Executor 加进程内 Lock(检查-记账原子), 并发测试 50 线程同 token → 恰 1 AUTHORIZED; **跨进程/多实例双花仍开放 = 生产 P0**(持久化 consumed 集 + 原子声明, 如 DB unique/Redis SETNX); key rotation/存储/executor-authorizer 信任关系 = 生产 TODO(已列, 原型不声称覆盖)
- 21:10 provenance/source-binding 类已加(yairsabag/Verb Authority 提出): grant 可选绑定 per-field source 标签(签名覆盖), executor fail closed: SOURCE_MISSING / SOURCE_MISMATCH(含 tool-output/peer 复用); 7 新测试; **诚实边界: 只能绑"声明的来源", 验证"来源是否诚实"= 上游 source-aware PEP(Verb Authority 类), 互补不重叠**
