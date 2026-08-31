# 💰 SpendShield — AI Agent 付款安全层

> **AI 替你花钱之前，先过 SpendShield 这关。**

让 AI Agent 下单、转账、调付费 API 之前，自动过四道闸门：
**干跑预览 → 预算上限 → 人工确认 → 全量审计。**

## 🩸 为什么会有这个项目（真实事故）

2026 年 8 月 9 日，我的自动化系统测试下单。

我传了 `dry: true`，以为只是试算价格。但服务器只认 `?dry=1` —— **4 单 99 元真实出码扣款，当天全部打水漂。**

这不是我一个人的坑。AI Agent 时代正在到来：Agent 替你订餐、替你充值、替你调付费 API——**当 AI 开始花真钱，谁给它上闸门？**

我把我踩过的坑，做成了一个库。

## ✨ 四道闸门

| 闸门 | 默认 | 作用 |
|------|------|------|
| 🧪 **dry_run** 干跑 | ✅ 开 | 只预览不执行——`dry` 参数失效也无所谓，库层面兜底 |
| 💰 **budget** 预算 | 不限 | 总预算超支直接拒绝，绝不超花 |
| 🚧 **max_amount** 单次上限 | 不限 | 单笔超限拦截（防"转 9999 给陌生人"） |
| 🙋 **approval** 人工确认 | 关 | 花钱前必须人点头（console / 回调） |
| 📜 **audit** 审计 | ✅ 开 | 每次尝试全留痕，导出 JSON 对账 |

## 🔑 身份层（KYA 最小实现，v0.4）

AI 没有法律人格，但必须有“数字身份”。每个 agent 注册专属策略，**未注册默认拒绝**：

```python
from spendshield import SpendShield, UnknownAgent

guard = SpendShield(dry_run=False)
guard.register_agent("mcd_bot", budget=50, max_amount=30,
                     blacklist=["测试收款"], whitelist=["麦当劳"],
                     rate_limit={"window_s": 60, "max_calls": 3})

@guard.protect("下单", agent="mcd_bot")   # 或运行时传 agent=xx

def place_order(amount, to):
    return call_real_api(amount, to)
```

- 未注册的 agent 调用 → 直接拒绝（`UnknownAgent`），审计留痕 `blocked_unknown_agent`
- `allow_unknown: true` 可回落全局策略（不推荐）
- 每条审计记录带 `agent` 字段：**谁在花、花给谁、用户知不知道**
- 策略即代码支持 `agents:` 段（YAML），预算/黑名单/频率/审批按 agent 隔离

## 🎯 意图一致性（防提示注入，v0.5）

AI 可能被劫持：提示注入、返利诱惑……闸门只知道“花多少、给谁”，不知道“这是用户要的吗”。
解法：**敏感操作强制人工确认**——即使没配全局审批，新收款方/大额也默认拦下：

```python
# 新收款方（从未交易过）→ 必须确认；没配审批通道 → 直接拒绝
# 金额 > approve_above → 必须确认
guard = SpendShield(approve_new_recipient=True, approve_above=1000)
```

- 交易成功的收款方自动进入记忆，之后不再反复烦你
- 白名单收款方永远跳过
- 未配置审批通道时，敏感操作**直接拒绝**（宁可拦死，不放行）
- 拦截记录 `blocked_approval` 带原因：新收款方 / 超阈值 / 未配置通道

## 🔐 密钥保险库（v0.6）

私钥不落地是 AI 支付的命门——**一次泄露，钱包被掏空**。密钥加密落盘，主密钥放环境变量，取用必须过闸门：

```bash
python -c "from spendshield import KeyVault; print(KeyVault.generate_key())"  # 生成主密钥(仅此一次)
export SPENDGUARD_MASTER_KEY=<刚才的输出>   # 放环境变量, 别写进代码/仓库
```

```python
from spendshield import SpendShield, KeyVault

vault = KeyVault("vault.json")              # 主密钥从环境变量读
vault.store("mcd_sk", "sk_live_xxxx")      # 加密落盘, 文件里只有密文

guard = SpendShield(key_vault=vault)
guard.register_agent("mcd_bot", whitelist=["mcd_sk"])
sk = guard.get_secret("mcd_sk", agent="mcd_bot")   # 过身份+意图闸门才能取
```

- 落盘文件无明文（AES128-CBC + HMAC）；主密钥不落盘
- 取密钥 = 敏感操作：未注册 agent 拒绝；新密钥名无审批通道默认拒绝（防提示注入偷密钥）
- 每次取用留审计 `secret_access`：谁、何时、取了哪个密钥

## 🚀 快速开始

```bash
pip install spendshield   # 或直接 clone 用
```

```python
from spendshield import SpendShield

guard = SpendShield(budget=200, dry_run=True, whitelist=["麦当劳"])   # 默认干跑 + 信任收款方

@guard.protect("下单")
def place_order(amount, to):
    return call_real_api(amount, to)            # 真实下单逻辑

# 干跑模式: 报错提示, 绝不真花
place_order(amount=99, to="麦当劳")
# => [SpendShield] dry_run: 下单 ¥99.0 -> 麦当劳 (未执行)
# => DryRunBlocked: 关掉 dry_run 才会真花

# 确认无误后放行, 预算闸门兜底
guard.dry_run = False
for i in range(4):
    place_order(amount=99, to="麦当劳")          # 第3单被 BudgetExceeded 拦住
```

> 💡 新收款方默认需人工确认(意图一致性, 防提示注入)——把常用收款方加白名单或注册 Agent 身份可免。

## 🎯 谁需要它

- **AI Agent 框架用户**：给你的 Agent 工具加装饰器，一行接入
- **自动化系统运维**：批量任务/定时下单，防误操作真扣款
- **MCP / Function Call 开发者**：LLM 生成的工具调用，过闸门再执行
- **所有被"测试单变真单"坑过的人** 🩸

## 🗺 Roadmap

- [x] v0.1 四道闸门 + 审计 + 装饰器接入
- [ ] 收款方黑名单/白名单（陌生收款方强制确认）
- [ ] 频率限制（同一收款方短时间 N 次）
- [ ] MCP server 版（Agent 工具调用直接过闸）
- [ ] 远程审批（企业微信/Telegram 确认）
- [ ] 多策略插件（风控规则引擎）

## 🧪 测试

```bash
python3 tests/test_guard.py   # 6 个测试全过
```

## 📝 反馈与贡献

- 🐛 遇到 Bug?[开一个 issue](https://github.com/felixpg13-glitch/spendshield/issues/new?template=bug_report.md)
- 💡 有想法?[提功能建议](https://github.com/felixpg13-glitch/spendshield/issues/new?template=feature_request.md)
- 🔒 发现安全漏洞?请看 [SECURITY.md](SECURITY.md)(请勿公开,私下报告)
- ⭐ 觉得有用,点个 star,让更多被坑过的人看到

## 📄 License

MIT — 拿去用。愿 AI 时代，没人再被"测试单"坑第二次。

---

**⭐ 如果这个项目对你有用，点个 star，让更多被坑过的人看到。**

## 🤖 MCP Server（AI Agent 直接调用）

让 Claude Code / OpenClaw 等 MCP 兼容 agent 直接通过工具调用过闸门：

```bash
# 启动(stdio 模式, agent 配置里指向它)
python -m spendshield.mcp_server --policy spendshield.yaml
# 或安装后: spendshield-mcp --policy spendshield.yaml
```

**工具**：
- `spend_protect(action, amount, to)` — 保护一次花钱操作（走全部闸门）
- `spend_status()` — 预算/已花/拦截统计
- `spend_audit(limit)` — 最近审计记录
- `spend_reset()` — 重置会话已花

```json
// agent 调用示例
{"name": "spend_protect", "arguments": {"action": "下单", "amount": 99, "to": "麦当劳"}}
// => {"ok": false, "reason": "[干跑] 下单 ¥99.0 -> 麦当劳 (未执行...)", "spent": 0.0}
```
