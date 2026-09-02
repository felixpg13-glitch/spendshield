# Verb Authority × SpendShield 交叉实验工件(2026-09-02)

> 对应 yairsabag/verb-authority#7(Beta testers wanted) + #2498 MCP 讨论

## ① 冻结输入
- `docs/va-scan/mcp-tools-list-v0.8.2.json` — 从 v0.8.2 源码(`f9a0c5b`)导出的 17 个 MCP 工具 / 36 参数, MCP tools/list 格式

## ② 精确命令
```bash
# 版本
python -m pip install "verb-authority==0.10.0b14"
# 导出(可选, 冻结文件已在 repo)
python -c "import sys; sys.path.insert(0,'.'); from spendshield.mcp_server import TOOLS; import json; json.dump({'tools':TOOLS}, open('mcp-tools-list.json','w'))"
# 扫描(离线, 无网络)
python -m verb_authority scan docs/va-scan/mcp-tools-list-v0.8.2.json --format json
```
- 独立复现: yairsabag 已从 pinned v0.8.2 源码逐字节复现报告 ✅

## ③ 所有者 authorship map(SpendShield 声明, 2026-09-02)
| 参数 | 属主 | 依据 |
|---|---|---|
| `recipient` / `to` | **application-owned** | 收款方是 authority 载体; enforcement 模式绑定进签名 grant |
| `amount` | **application-owned**(enforcement 模式) | 来自订单/购物车/报价状态, 签发时绑定; advisory 模式(agent 自拟)= 已知弱点, VA trusted_args 正是补它的层 |
| `purpose` | **model-writable** | 自由文本意图描述, 无权威 |
| `agent` / `agent_id` | **application-owned** | 会话身份绑定 |
| `approval_id` / `draft_id` / `version` / `by` | **application-owned** | 工作流状态, 模型不可杜撰 |
| policy 内容(policy_create/apply) | **application-owned + 人工 review** | lifecycle 设计上强制 REVIEWED 才能 APPLY |

## ④ 包版本/hash
- verb-authority==0.10.0b14
- spendshield v0.8.2 = `f9a0c5bf424a713edb2fbb92a44f4ec3479c3ff4`(本工件文档 commit 见 git)

## 预期实验(0/1 测试)
- 宿主经 trusted path 提供 amount=20(application-owned)
- 模型通道尝试 amount=200 → **0 次工具调用**(VA runtime 拦截)
- 应用精确提供 amount=20 → **1 次工具调用**
