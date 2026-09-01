# SpendShield 策略文档(V2 Policy Engine)

策略即代码: 所有规则收敛在 YAML, `SpendShield().load_policy("policy.yaml")` 加载。
V1 扁平格式(budget/max_amount/blacklist/...)仍兼容, 自动迁移到 V2 schema。

## V2 格式(YAML)

```yaml
version: "2.0.0"            # 必填; 每次评估记录版本, 事后可复现

policy:                     # 全局策略
  budget:
    daily: 100              # 每日上限(0 = 不限)
    monthly: 1000           # 每月上限
    total: 0                # 总上限
  transaction:
    max: 50                 # 单笔上限
    min: 0.01               # 单笔下限(防 0 元/负元/极小刷单)
  merchants:
    allowed: [amazon.com]   # 白名单: 精确域匹配(子域默认信任); 空 = 不限制
    blocked: [scam.com]     # 黑名单: 子串匹配, 优先于白名单
    allow_subdomains: true  # checkout.amazon.com 视为 amazon.com
  approval:
    over: 30                # 单笔 > $30 需人工确认
    new_merchant: true      # 新收款方需人工确认
    channel: tg             # console / tg / webhook / callable; 空 = 无通道 → 安全默认拒绝
  rate_limit:
    window_s: 3600          # 窗口(秒)
    max_calls: 5            # 窗口内最多笔数
    max_total: 300          # 窗口内累计金额上限

agents:                     # 按 Agent 分策略(agent 级 ⊳ 全局, 写了就整段覆盖)
  shopping-agent:
    transaction: { max: 50 }
    approval: { over: 30, new_merchant: true, channel: tg }
```

## 合并规则(CSS 式)

- agent 写了某段(如 budget)→ 整段覆盖全局
- 没写的字段继承全局
- 黑名单 = 合并(agent + 全局, 只会更严)
- agent 的 approval.channel 为空 → 回退全局通道

## 决策三态

| 决策 | 含义 | 后续 |
|---|---|---|
| ALLOW | 通过, 预算已预留 | 可付款 |
| APPROVAL | 需人工确认 | `shield.approve(id)` / `shield.reject(id)`; 批准后按当前策略重新评估 |
| DENY | 拒绝(带 RuleHit 解释) | 查看 reason/rules |

## 审计

每次评估(含被拦)全部留痕, 带 policy_version。
`guard.export_audit("audit.json")` 导出; `python -m spendshield.dashboard --file audit.json` 可视化。

## 安全默认

- 未注册 Agent(非空未注册)→ 拒绝; 空 agent(匿名)→ 全局策略
- 新收款方/大额无审批通道 → 拒绝
- 策略文件校验失败 → 拒绝加载(不进入半可用状态)
- 运行时篡改策略对象 → 指纹校验拒绝
- 策略变更 → 挂起审批全部作废(防宽松窗口挂单, 收紧后花钱)
- 幂等键(idempotency_key)→ 同 key 重放拒绝
