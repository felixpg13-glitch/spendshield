# 我让 AI 花了 99 块冤枉钱,然后写了个开源库给它上锁

> 一个真实事故:测试单变成了真扣款,4 单 99 元。我把这个教训做成了开源项目。

## 事故经过

2026 年 8 月 9 日,我的自动化系统要"测试下单"。我传了 `dry: true`,以为只是试算价格。

但服务器只认 URL 里的 `?dry=1`——body 里的参数根本没生效。

**4 单 99 元,真实出码,扣款成功。** 钱没了。

损失不大,但我意识到一件不舒服的事:**这个 bug 马上会在更大的规模上重演**。

AI Agent 正在开始替我们订餐、买算力、调付费 API。每一次都是一个新的"测试 flag 悄悄失效"的现场——预算被突破、提示注入的 agent 未经授权乱花钱。

**当 AI 开始花真钱,谁给它上闸门?**

我把伤疤做成了一个库。

## 它做什么

```python
from spendshield import SpendShield

guard = SpendShield(budget=200, dry_run=True, whitelist=["麦当劳"])

@guard.protect("下单")
def place_order(amount, to):
    return call_real_api(amount, to)   # 真实支付代码

place_order(amount=99, to="麦当劳")
# => DryRunBlocked: 干跑模式,什么都没执行

guard.dry_run = False
for i in range(4):
    place_order(amount=99, to="麦当劳")
    # 第 3 单被 BudgetExceeded 拦下
```

四道确定性闸门:干跑 / 预算 / 单次上限 / 人工审批,加全量审计。**规则是代码,不是 AI 的意见**——Agent 无法争辩、欺骗或注入绕过。

## 三根柱子(为什么它不只是个包装)

**1. 身份(KYA)**——每个 Agent 注册自己的预算/黑名单/频率限制。**未注册的 Agent 默认拒绝。**

```python
guard.register_agent("mcd_bot", budget=50, max_amount=30,
                     blacklist=["未知收款方"], whitelist=["麦当劳"])
```

**2. 意图一致性(防提示注入)**——新收款方、大额支付**必须人工确认**。被提示注入劫持的 Agent 想给陌生人转账,会被"人没点头"卡住。

**3. 密钥保险库**——密钥 AES-256 加密落盘,主密钥永不落盘,取用过闸门+全审计。告别"私钥写在配置文件里"。

## x402 兼容(面向未来)

[x402](https://x402.org) 是新兴的互联网支付协议(HTTP 402)——Agent 付费调 API 的方式。SpendShield 已适配:**支付结算前先过闸门**。

```python
from spendshield.adapters.x402 import X402PaywallGuard

pw = X402PaywallGuard(guard)
pw.authorize_resource("weather-api", price="0.01", asset="USDC", pay_to="0x...")
```

x402 让 Agent 能付钱,SpendShield 让 Agent 不乱付钱。

## 项目现状

- 36 个测试,身份/意图/密钥/闸门全覆盖
- MCP Server,支持 Claude Code / OpenClaw 等
- Dockerfile 已备
- MIT 开源

这是一个**今天还是 0 star** 的项目。一天之内,从一个真实的伤疤长出来,为一个我确信会来的问题。

如果你也被"测试单"坑过,或者你在做会碰钱的 Agent——欢迎反馈。

⭐ [GitHub: felixpg13-glitch/spendshield](https://github.com/felixpg13-glitch/spendshield)
📦 `pip install spendshield`
