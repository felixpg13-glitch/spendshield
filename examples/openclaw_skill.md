# OpenClaw 接入 SpendGuard

在 OpenClaw 中给工具调用加付款护栏：用 MCP server 方式注册。

## 步骤

1. 启动 MCP server（后台）：
   ```bash
   spendguard-mcp --policy spendguard.yaml
   ```

2. 在 OpenClaw 配置中注册 MCP 工具，agent 即可调用：
   - `spend_protect(action, amount, to)` — 花钱前先问护栏
   - `spend_status()` — 预算/已花
   - `spend_audit(limit)` — 审计

3. Agent 工作流示例：
   ```
   用户: 帮我订一份麦当劳
   Agent: (调用 spend_protect {action: 下单, amount: 99, to: 麦当劳})
   SpendGuard: {"ok": false, "reason": "[干跑] 未执行..."}
   Agent: 需要先关闭干跑并确认预算，请管理员操作
   ```

## 安全提示

- 默认 dry_run=True（只预览不真花）
- 生产环境建议: 配 budget + 黑名单 + approval=webhook(人工审批服务)
