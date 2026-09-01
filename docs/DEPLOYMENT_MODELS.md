# Deployment Models — 部署形态与信任边界

> 2026-09-02 Felix 定稿:产品梯度 SDK → MCP → Gateway → 成熟资金治理平台。
> 原则:任何文案不得越过信任边界(「最简单接入方式」≠「接入完成」)。

## 四层形态(从简到强)

### 1. SDK(库,嵌入代码)
```python
shield = SpendShield(budget=100, max_amount=50)

@shield.protect("order")                    # 最简单接入方式
def place_order(amount, to): ...

r = shield.authorize(agent, 75, "amazon.com")   # 或手动调用
```
- **信任边界**:只保护「经过装饰器/手动调用的路径」。若调用方绕过函数直接调底层 API,SpendShield 看不到。
- **适用**:单服务、信任自己的代码调用方、接入成本最低。

### 2. MCP(Agent 主动询问)
```
Agent → SpendShield MCP → ALLOW / APPROVAL / DENY
```
- Agent 的工具列表里出现 `check_transaction(amount, merchant)`,花钱前**自己问**。
- **信任边界**:这属于 **Agent 主动调用 Guardrail**——依赖 Agent 听话。不解决「Agent 不听话、绕过检查直接调支付」的问题。
- **适用**:让 AI 原生工作流「自觉合规」,教育型/演示型场景。

### 3. Gateway(强制拦截,生产级)
```
Agent
  ↓
Payment Gateway
  ↓
SpendShield   ← 所有请求必须经过这里
  ↓
ALLOW / DENY
  ↓
Payment
```
- **信任边界**:支付入口只有一条路,SpendShield 挂在必经之路上 → **每笔都经过由架构保证**,不靠写代码自觉、不靠 Agent 听话。
- **适用**:真实资金、多 agent、需要「不能绕过」的保证。这是「Agent 不听话」问题的正解。

### 4. 成熟资金治理平台(未来)
- 集中政策管理、跨服务审计、审批流、风控模型、合规报告。
- 不是现在要做的事——等真实用户需求驱动。

## 定位一句话
- **Colab / demo** = 「60 秒亲眼看到 DENY」——漏斗最前端
- **SDK / MCP** = 现在的产品形态
- **Gateway** = 生产级目标(需要真实用户/真实支付场景驱动)
- 营销文案纪律:**说「最简单的接入方式」,不说「接入完成」**;生产环境还涉及支付入口防绕过、Policy 权限、部署安全。
