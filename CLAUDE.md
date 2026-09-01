# CLAUDE.md — SpendShield 开发指南(给 Claude Code / AI 协作者)

## 项目是什么

**SpendShield = AI Agent 的资金 Policy/Authorization Layer。** 让 AI Agent 可以花钱,但不能乱花钱。
不是钱包、不是支付平台:站在 Agent ↔ 钱 中间,决定「这笔支付该不该发生」,用确定性、可解释的规则。

产品主线一句话:**让 AI Agent 能够安全地使用真实资金。**

## 架构速览

```
spendshield/
├── guard.py              # 主入口 SpendShield 类(所有路径最终都走 V2 引擎)
├── policy/               # V2 Policy Engine(评估管线)
│   ├── schema.py         #   数据模型(Policy/AgentPolicy/RuleHit/EngineState)
│   ├── validator.py      #   策略校验(坏 policy 拒绝加载, 快速失败)
│   ├── engine.py         #   评估管线: merchant→amount→budget→rate→approval(纯函数)
│   ├── simulator.py      #   PolicySimulator(单点/扫描/矩阵, 不真花)
│   └── versioning.py     #   策略快照/回滚/diff
├── adapters/x402.py      # x402 支付协议适配(服务端+客户端)
├── mcp_server.py         # MCP server(10 个工具, AI Agent 直接调用)
├── vault.py              # 密钥保险库(Fernet 加密, 取用过闸门)
└── tests/
    └── security/         # 19 个攻击套件(116 测试): 8 攻击面 + 宪法 + Fuzz + Hardening
```

核心 API:

```python
from spendshield import SpendShield
shield = SpendShield()
shield.load_policy("policy.yaml")
result = shield.authorize(agent="bot", amount=75, to="amazon.com")
# result.decision: ALLOW / APPROVAL / DENY + reason + rules(RuleHit)
```

## ⛔ 安全宪法 8 条(不可违反, V3+ 任何层都不能破坏)

1. 未授权 → 不能付款
2. 超预算 → 不能付款(`spent <= budget` 永真)
3. Approval 不匹配 → 不能付款
4. 无效身份 → 不能付款
5. 重放 → 最多一次有效授权
6. 并发 → 不能突破预算
7. 引擎故障 → 默认拒绝(fail-closed)
8. Agent 不能绕过 SpendShield

## 开发纪律(强制)

每轮开发走:**功能 → 单测 → 集成 → Security → 并发 → Fuzz → 故障 → Sandbox → Review → Release**
- **P0/P1 安全 bug 不发布。** 发布前问:「这次改动有没有给攻击者增加一条花钱路径?」不确定就不发。
- 发现安全 bug → 修复 → **必须加永久回归测试**(tests/security/ 新增用例)。
- **不追功能数量**,追:真实 Agent 数 / 交易数 / 保护金额 / 拦截金额 / 留存。

## 踩过的坑(别重犯)

- **双账不同步**:任何「手动记账」路径(如 `_spent += x`)必须走 `guard.book()` 同步 V2 state——否则预算失效(历史两个严重漏洞都源于此)。
- **双路径不一致**:get_secret 等旧闸门路径必须与主引擎共享名单/审批语义。
- **策略变更是安全事件**:换 policy 时挂起审批全部作废(防宽松窗口挂单→收紧后花钱)。
- **迁移/转换代码异常不能静默吞**(except 里也要 print)——静默跳过 = 行为悄悄退化。
- **运行时篡改 policy 对象**:有指纹校验(sha256),篡改 → DENY "policy tampered"。改配置走 `load_policy`,别直接改对象。
- **空 agent = 匿名走全局**(合法);非空未注册 agent = 拒绝。别把两者混了。
- **白名单是精确域匹配**(`notamazon.com` 不能命中 `amazon.com`);黑名单是子串;信任前缀含 `.` 用域边界匹配。
- **浮点误差方向是安全的**(只多拒不超支),但 NaN/Inf/空值必须前置拦截。

## 测试

```bash
python3 -m pytest tests/            # 全量(当前 186 全绿)
python3 -m pytest tests/security/   # 攻击套件(116)
```

Simulator 与真实 authorize 语义必须严格一致——改引擎时跑 `test_hardening_sim_consistency.py`(800 笔随机差分)。

## 发布流程

1. `__init__.py` + `pyproject.toml` 版本号同步改(build 读 pyproject!)
2. 七步验证: build → clean install → API smoke → full regression → fuzz/security → package integrity(扫敏感泄漏)→ release
3. PyPI: `twine upload dist/spendshield-*`(token 走钥匙串 pypi-token, 用户名 `__token__`)
4. ⚠️ 打包用 `[tool.setuptools.packages.find] include = ["spendshield*"]`——显式 packages 列表会漏子包(0.6.x 血泪)

## ⚠️ 真实世界验证优先于功能开发(2026-09-01 Felix 定)

- **「实现 ≈ 设计」不等于「设计 ≈ 真实需求」**。测试全绿只证明我们符合自己的设计。
- 没有 PMF 前不做 enterprise features。0.8 已冻结功能开发。
- 优先回答: 有没有真实的人/团队愿意把真实钱流量交给 SpendShield?
- 每个真实场景跑完记录 7 问(见 `docs/REALITY_TEST.md`), 进 Corpus。
- 外部红队是硬要求: 黑盒攻击者拿公开 API 让 $50 变 $5000, 才是真正的压力测试。

## 迭代规则(每版本必答 5 问)

1. 新增了什么能力?
2. 新增了什么攻击面?(每新增能力必须配攻击模型)
3. 新增了哪些安全不变量?
4. 新增了多少 Regression Cases?(进 `docs/attack-corpus.md` 编号登记)
5. 旧测试有没有全部保持通过?

**核心心法**: 迭代单位 = 风险覆盖能力, 不是功能数量。
永远问:「SpendShield 还在哪些情况下可能错误地让一笔钱通过?」
→ 变成攻击样本 → 不变量 → 自动测试 → 永久回归(见 `docs/attack-corpus.md`, 37 个编号案例)。

## Roadmap 现状

V1 ✅ → V2 Policy Engine ✅ → V2.1 Simulator ✅ → V2.2 Security Harness ✅ → 引擎切换 ✅ → MCP ✅ → **v0.7.0 已发 PyPI** → 下一个: 第一个真实 Agent(麦当当) → V3 Intent Layer(防 prompt injection)→ V4 Risk → V5 IAM → V6 Payment Rails → V7 Dashboard → V8 Enterprise

设计文档: `docs/V2_POLICY_ENGINE.md` / `docs/policy.md`(DSL 手册)
