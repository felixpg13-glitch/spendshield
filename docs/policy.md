# SpendShield 策略文档

策略即代码：所有规则收敛在 YAML 文件，`SpendShield(policy="spendshield.yaml")` 加载。

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `dry_run` | bool | 干跑模式(默认 true)，只预览不真花 |
| `budget` | number | 总预算，0=不限 |
| `max_amount` | number | 单次上限，0=不限 |
| `blacklist` | list | 收款方黑名单(子串匹配)，命中直接拒绝 |
| `whitelist` | list | 收款方白名单(子串匹配)，跳过人工确认 |
| `rate_limit.window_s` | number | 频率窗口(秒) |
| `rate_limit.max_calls` | number | 窗口内最大次数 |
| `approval` | null/console/tg/webhook | 人工确认模式 |
| `tg.token` / `tg.chat` | str | TG 远程审批配置 |
| `webhook_url` | str | Webhook 审批地址 |
| `allow_unknown` | bool | 未注册 Agent 是否回落全局策略(默认 false = 拒绝) |
| `agents` | dict | 按 Agent 分策略: {id: {budget/max_amount/blacklist/whitelist/rate_limit/approval}} |
| `approve_new_recipient` | bool | 意图一致性: 新收款方强制审批(默认 true) |
| `approve_above` | number | 意图一致性: 超此金额强制审批(0=不限) |
| `vault.path` / `vault.master_key_env` | str | 密钥保险库: 文件路径 + 主密钥环境变量名 |

## 审批模式

- `null` — 不需要人工确认(低风险场景)
- `console` — 终端输入 y/n
- `tg` — 发 TG 消息等回复(需 tg.token/tg.chat)
- `webhook` — POST 到审核服务，响应 `{"approved": bool}`
- callable — 代码回调

## 安全默认

- 未知审批模式 = 拒绝(安全默认)
- 未注册 Agent = 拒绝(UnknownAgent)
- 新收款方/大额无审批通道 = 拒绝(意图一致性)
- 主密钥不落盘; 取密钥过闸门
- 拦截全部留痕审计
