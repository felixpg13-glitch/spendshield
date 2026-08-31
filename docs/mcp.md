# SpendShield MCP Server

让 Claude Code / OpenClaw 等 MCP 兼容 agent 直接调用付款护栏。

## 启动

```bash
python -m spendshield.mcp_server --policy spendshield.yaml
# 或安装后: spendshield-mcp --policy spendshield.yaml
```

## 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `spend_protect` | action, amount, to, agent | 保护一次花钱操作(走全部闸门, 带 Agent 身份) |
| `spend_status` | — | 预算/已花/拦截统计(含按 Agent) |
| `spend_audit` | limit | 最近审计记录(含谁在花) |
| `spend_reset` | — | 重置会话已花 |
| `secret_get` | name, agent | 从密钥保险库取密钥(过闸门 + 审计) |

## Claude Code 配置

见 `examples/claude_code_mcp.json`，把路径换成你的策略文件。
