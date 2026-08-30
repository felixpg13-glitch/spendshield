# SpendGuard MCP Server

让 Claude Code / OpenClaw 等 MCP 兼容 agent 直接调用付款护栏。

## 启动

```bash
python -m spendguard.mcp_server --policy spendguard.yaml
# 或安装后: spendguard-mcp --policy spendguard.yaml
```

## 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `spend_protect` | action, amount, to | 保护一次花钱操作(走全部闸门) |
| `spend_status` | — | 预算/已花/拦截统计 |
| `spend_audit` | limit | 最近审计记录 |
| `spend_reset` | — | 重置会话已花 |

## Claude Code 配置

见 `examples/claude_code_mcp.json`，把路径换成你的策略文件。
