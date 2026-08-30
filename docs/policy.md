# SpendGuard 策略文档

策略即代码：所有规则收敛在 YAML 文件，`SpendGuard(policy="spendguard.yaml")` 加载。

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

## 审批模式

- `null` — 不需要人工确认(低风险场景)
- `console` — 终端输入 y/n
- `tg` — 发 TG 消息等回复(需 tg.token/tg.chat)
- `webhook` — POST 到审核服务，响应 `{"approved": bool}`
- callable — 代码回调

## 安全默认

- 未知审批模式 = 拒绝(安全默认)
- 拦截全部留痕审计
