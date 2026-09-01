# SpendShield V2 — Policy Engine 设计文档

> 日期: 2026-09-01 | 状态: 设计定稿, 待实现 | 来源: Felix 提供路线(ChatGPT 起草, 玄机落地成可写码设计)
>
> **产品主线(所有功能围绕这一句)**: 让 AI Agent 能够安全地使用真实资金。
>
> **V2 目标**: 把 V1 散落的四道闸门(budget/max_amount/名单/approval)统一成一个 **Policy**——
> 开发者只写配置, SpendShield 自己判断。

## 0. 路线图(不跑偏)

```
V1 防止乱花(现状 ✅) → V2 Policy Engine(本文档) → V2.1 Simulator → V2.2 Security Test Suite
→ V3 Intent Layer → V4 Risk Engine → V5 Agent Identity/Delegation → V6 Payment Rails
→ V7 Dashboard → V8 Enterprise
```

**每个版本必须有验证目标, 不是 feature checklist**:
- V2: 有没有 5 个开发者愿意写 Policy?
- V3: 有没有真实 Agent 因 intent mismatch 被成功拦截?
- V4: Risk Engine 能不能减少误报?
- V6: 有没有真实支付通过 SpendShield?
- 真正要追的指标: Agent 数 → Transaction 数 → Protected $ → 留存开发者。Star 是附带指标。

## 1. 设计原则

1. **规则优先, 不碰 LLM**: V2 全部 deterministic。ML/AI 留到 V4 Risk Engine。
2. **向后兼容**: 现有 30 个测试 + `protect()` 装饰器 + `load_policy()` 全保留, 内部换引擎。
3. **决策三态**: `ALLOW` / `DENY` / `APPROVAL`。APPROVAL 是挂起态, 批准后**重新评估**。
4. **解释即产品**: 每个决策必须能回答「为什么」。这是差异化亮点, 不是装饰。
5. **审计默认**: 每次评估(含被拦)全落盘, 记录当时的 policy_version。

## 2. 技术架构

```
spendshield/
├── guard.py              # 现有入口(保留) — 内部委托 policy 引擎
├── policy/
│   ├── __init__.py       # 公开 API: Policy / PaymentRequest / PolicySimulator
│   ├── schema.py         # Policy/AgentPolicy/Rule 数据模型(dataclass)
│   ├── validator.py      # 启动即校验: 坏 policy 拒绝加载(快速失败)
│   ├── engine.py         # 评估管线: 规则链 → 决策(纯函数, 无副作用)
│   ├── explanation.py    # 解释生成: 命中规则 + 数值 + 人类可读原因
│   ├── versioning.py     # 版本快照 / 回滚 / diff
│   └── simulator.py      # V2.1: 模拟器(单点 / 扫描 / 矩阵)
├── tests/
│   ├── test_policy_*.py  # 单元测试
│   └── security/         # V2.2: 攻击测试套件
└── mcp_server.py         # 扩展 MCP 工具: policy_apply / policy_sim
```

**评估管线(纯函数, 无副作用, 便于测试)**:

```
PaymentRequest(agent, amount, to, meta)
  → resolve_agent        # 身份解析: 未知 agent → DENY(安全默认)
  → merge_policy         # agent 策略 ⊳ 全局策略(agent 优先)
  → gate_merchant        # 名单规则: blacklist → DENY; whitelist → 标记可信
  → gate_amount          # max_transaction / min_transaction
  → gate_budget          # 日/月/总预算(含本轮)
  → gate_rate            # 频率限制(窗口内次数 / 金额)
  → gate_approval        # approve_over / new_merchant → APPROVAL
  → decision             # ALLOW / DENY / APPROVAL
  → explain              # 生成解释
  → audit                # 落盘(带 policy_version)
```

## 3. Policy DSL(YAML)

```yaml
# policy.yaml
version: "2.0.0"            # 必填, versioning 依赖

policy:
  budget:
    daily: 100
    monthly: 1000
    total: 0                # 0 = 不限
  transaction:
    max: 50                 # 单次上限
    min: 0.01               # 单次下限(防 0 元 / 负元)
  merchants:
    allowed: [amazon.com, walmart.com]    # 白名单(域匹配)
    blocked: []             # 黑名单
    allow_subdomains: true  # *.amazon.com 视为 amazon.com
  approval:
    over: 30                # 单笔 > $30 需人工
    new_merchant: true      # 新收款方需人工
    channel: tg             # console / tg / webhook / callable
  rate_limit:
    window_s: 3600
    max_calls: 20
    max_total: 500          # 窗口内累计金额上限(可选)
  time_window:              # 可选: 仅允许时段
    allow: ["06:00-23:59"]

agents:
  shopping-agent:
    budget:
      daily: 100
      monthly: 1000
    transaction:
      max: 50
    merchants:
      allowed: [amazon.com, walmart.com]
    approval:
      over: 30
      new_merchant: true
```

**合并规则(类似 CSS)**: agent 级 > 全局级; 未写的字段继承全局; 写了就整段覆盖。
**规则 id 规范**: 每条规则固定 id(`max_transaction` / `daily_budget` / ...), explanation 用它引用。

## 4. Python API

```python
from spendshield import SpendShield

shield = SpendShield(policy="policy.yaml")

# 核心: 授权(返回结果对象, 不抛异常)
result = shield.authorize(
    agent="shopping-agent",
    amount=75.0,
    to="amazon.com",
    meta={"order_id": "x1", "currency": "USD"},
)
result.decision    # "ALLOW" | "DENY" | "APPROVAL"
result.reason      # "transaction $75.00 exceeds max $50.00"
result.rules       # [RuleHit(rule="max_transaction", expected=50, actual=75, operator=">")]

# 审批流(APPROVAL 态)
approval_id = result.approval_id       # 自动生成
shield.approve(approval_id, by="felix")    # 批准 → 重新评估 → 返回新 result
shield.reject(approval_id, by="felix")     # 拒绝

# 装饰器(向后兼容, DENY 时抛 BudgetExceeded / NeedsApproval)
@shield.protect("下单", agent="shopping-agent")
def place_order(order_id, amount, to): ...

# 旧 API 全保留: load_policy / get_secret / summary / export_audit
```

**AuthorizationResult 字段**:
- `decision`: ALLOW / DENY / APPROVAL
- `reason`: 人类可读(给用户)
- `rules`: [RuleHit] 结构化(给 LLM / UI)
- `approval_id`: APPROVAL 时生成
- `request`: 原始请求回显
- `policy_version`: 评估用的版本
- `ts` / `audit_id`

## 5. Policy Explanation(重点 ⭐)

> 目标: 用户看到结果 3 秒内知道「为什么、哪个规则、差多少」。不要只告诉用户 `DENIED`。

**RuleHit 结构**:

```python
@dataclass
class RuleHit:
    rule: str          # 规则 id: "max_transaction"
    operator: str      # ">" | "<" | "in" | "not_in" | ...
    expected: Any      # 规则阈值: 50
    actual: Any        # 实际值: 75
    severity: str      # "info" | "warn" | "block"
    message: str       # 人类可读: "transaction $75 exceeds the $50 limit"
```

**生成规则**:
- DENY = 所有命中规则 message 拼接, 首个致命规则置顶
- ALLOW = 「通过」+ 命中的 warn 级规则(如 "approaching daily budget 80/100")
- APPROVAL = 挂起原因 + 如何批准
- 每条规则声明自带消息模板(中英双语), 数值由 RuleHit 填充

**示例输出(终端)**:

```
❌ DENIED
Reason: transaction $75.00 exceeds agent's $50.00 limit
Rules:
  - max_transaction: $75 > $50 (block)
```

## 6. Versioning

- 每个 policy 文件带 `version` 字段
- 加载时快照到 `.spendshield/policies/{version}.yaml`
- `PolicyManager`: `list_versions()` / `rollback("2.0.0")` / `diff("2.0.0", "2.0.1")`
- 每次 evaluate 记录 `policy_version` 进审计 → 事后可复现「当时为什么放行」
- 实现: 纯文件快照, 不引数据库

## 7. Simulator(V2.1)

```python
from spendshield.policy import PolicySimulator

sim = PolicySimulator(policy="policy.yaml")

# 单点模拟
sim.evaluate(agent="shopping-agent", amount=75, to="amazon.com")
# → Decision(decision="DENY", rules=[max_transaction $75>$50])

# 金额扫描(找边界)
sim.sweep(agent="shopping-agent", to="amazon.com", amounts=[20, 50, 51, 80])
# → {20: ALLOW, 50: ALLOW, 51: DENY, 80: APPROVAL}

# 全规则矩阵(列出所有规则 + 测试用例建议, 可导出 pytest)
sim.matrix()
```

用途: 开发期验证 policy、README 演示、CI 快照回归。让 Policy 从「配置文件」变成「可测试的安全系统」。

## 8. Security Test Suite(V2.2)

`tests/security/` 每个文件一个攻击面, 全部自动化:

| 文件 | 攻击 | 断言 |
|---|---|---|
| `budget_bypass.py` | 预算 $100 并发两笔 $60 | 总额 ≤ $100 |
| `race_condition.py` | 10 线程同时扣预算 | 最终不超 |
| `replay_attack.py` | 同一 payment / approval id 重放 | 拒绝 |
| `double_spend.py` | APPROVAL 批准后重复执行 | 拒绝(幂等) |
| `policy_bypass.py` | 空 agent / 未知 agent / 负金额 / 0 金额 / 巨大金额 | 安全默认 |
| `approval_bypass.py` | 审批通过后改金额 / 改收款方 | 重新评估 |
| `parameter_tampering.py` | "Amazon.com" / " amazon.com " / "amazon.com.evil" | 名单绕过 = 失败 |
| `credential_leak.py` | 异常 / 日志含 token / 密钥 | 脱敏 |

**并发正确性**: 预算扣减用锁 + 评估-提交事务(评估在锁内做, 防 TOCTOU)。
**铁律**: 这个项目以后保护的是真钱, 安全可信度 > feature 数量。

## 9. 迁移路径(外科手术式)

1. 新建 `policy/` 包, 不碰 guard.py 现有逻辑
2. 写 schema → validator → engine → explanation → versioning
3. guard.py 的 `_authorize` / `_check` 改为委托 engine(**先双跑对比, 再切换**)
4. `load_policy` 兼容: 旧 YAML 格式 → 自动转新 schema(migrator)
5. 全部 30 个旧测试保持绿 + 新增 policy 测试
6. MCP 加 `policy_apply` / `policy_sim` 工具

## 10. V2 完成定义(验证目标)

- [ ] 5 个示例 policy(README + examples/)
- [ ] Policy DSL 文档 + 模板库
- [ ] 单测 ≥ 40(含 security suite)
- [ ] explanation 有人看得懂(README 截图)
- [ ] 向后兼容: 旧配置能跑, 旧测试全绿
- [ ] 5 个开发者愿意写 Policy(最终衡量)

## 11. 后续版本锚点(先不实现, 但架构要留位)

- **V3 Intent Layer**: intent → plan → payment intent → actual payment 四层绑定, 防 prompt injection 买 $2000 VIP
- **V4 Risk Engine**: 先 deterministic 评分(金额/新商家/未知商家), 以后再加 ML
- **V5 Agent Identity**: 正式 IAM + Human→Agent 支付权限委托(delegation)
- **V6 Payment Rails**: Stripe / x402 / Stablecoin / Wallet 全接, 但 Payment Provider 永远在下游, 不是核心
- **V7 Dashboard**: 等 V2 跑通再考虑; 第一版只要 Agents/Budgets/Policies/Transactions/Approvals/Audit/Risk 七块
- **V8 Enterprise**: 有真实用户再说(Team/Roles/SSO/Compliance)

> 架构预留: engine 的 gate 链是插拔式的, 以后加 IntentGate / RiskGate 就是新增一个 gate 类, 不动评估骨架。
