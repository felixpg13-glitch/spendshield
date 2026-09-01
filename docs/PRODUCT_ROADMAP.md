# SpendShield Product Roadmap(2026-09-02 Felix 定稿)

> 由真实需求驱动,每一步验证过了再走下一步。**禁止跳级。**
> 核心验证问题:「一个陌生开发者,能否 60 秒理解 SpendShield,10 分钟接起来?」

## 产品一句话
AI Agent 的资金控制/支付 Guardrail——**不是帮 Agent 花钱,是花钱前先判断**(ALLOW / APPROVAL / DENY,允许的才继续)。v0.8.0 已从 max_amount 进入 Policy 治理与审计。

## 接入四形态(现役 → 未来)
1. **SDK/Decorator** — `pip install` + `@shield.protect(...)`(最简单,Python 开发者)
2. **手动 authorize()** — 代码任意位置检查(灵活)
3. **MCP Server** — Agent 主动问 SpendShield(AI-native,第二阶段重点)
4. **Gateway** — 所有支付请求强制过 SpendShield(生产级,解决「绕过」,第三阶段)

## 用户漏斗(当前最重要的产品路径)
```
看到 GitHub/Glama/HN/Reddit → ⚡60秒 Colab → 第一次看到 DENY → 「有意思」
→ pip install → 5-10 分钟接入 → 真正保护 Agent
```
优化核心 = **降低 Time-to-First-DENY**(已从 10-30 分钟压到 <60 秒)。

## 安全边界(不夸大)
- **能防**:Agent 违规消费 / 未授权 Policy 修改 / Runtime Policy 篡改检测 / 版本回滚审计
- **不承诺**:绕过 SpendShield 直连底层支付 API / 攻击者已拿服务器控制权 → 属部署架构与基础设施安全

## 五阶段路线(每步由真实需求推动)
1. **第一阶段(现在)**:SDK 做到「真能用」——第一个陌生开发者成功接入
   - Colab Run All → DENY ≤60s ✅(已完成)
   - README 看→玩→装 ✅(已完成)
   - Quickstart 5-10 分钟接入 ✅(已备)
   - **麦当当场景跑完整真实接入 ⏳(明天主线)**
   - 记录真实开发者 pip install → 第一次 DENY 的时间
   - 找出 5-10 分钟里最烦的一步 → 针对性自动化
   - 收集第一个真实用户反馈;不做 UI
2. **第二阶段:SDK → Agent** — MCP 重点:安装模板/Claude 配置示例/常用 Policy 模板/Agent transaction tool/清晰 DENY reason;目标是开发者让 Agent 直接拥有 guardrail
3. **第三阶段:解决「绕过」** — Agent → Payment Gateway → SpendShield → ALLOW/DENY → Payment Provider;SpendShield 成为强制控制点
4. **第四阶段:Policy Governance** — 企业关心「谁批准了 5 万美元额度/什么时候改的/为什么改/跑哪个版本/能否立即 rollback/审计完整记录」;工具 → 基础设施
5. **第五阶段:产品化** — Connect Agent → Set $50 limit → Choose policies → Enable Protection,用户不写代码不写 YAML

## 路线纪律
- **不是** SDK → 一堆功能 → SaaS;而是 **SDK → 用户 → Agent → 强制入口 → Governance → Platform**
- MCP 有用户才做 Gateway;企业问审计/审批/团队权限才做集中式平台
- 现在最该验证的是小问题:60 秒理解 + 10 分钟接入;**别跑太快,别急着做 UI/大平台**
