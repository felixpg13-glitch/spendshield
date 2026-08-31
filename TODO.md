# SpendShield TODO（2026-08-31 02:03 定: 完善后再发）

## 定位
AI Agent 付款安全层（99 元事故的产物）。Python 生态 + 血泪故事 = 差异化。
竞品全 2026 年刚起步无赢家（preloop 55⭐/payment-guard 3⭐/black-vault 8⭐），等风来（AP2/x402 标准成形中）。

## 现状 v0.1（已就绪）
- ✅ 四道闸门: dry_run(默认) / budget / max_amount / approval(console/callback) + 全量审计导出
- ✅ 6 测试全过, demo(99元事故还原), README 故事性, pyproject 可 pip 装
- 位置: /Users/book/projects/spendshield/

## 完善清单（竞品差距分析 02:03）
- [ ] **MCP server 版**(最优先): Python 生态 MCP 支付护栏没人做, 形态对齐 payment-guard
- [ ] **策略即代码**: YAML 声明式规则(预算/黑名单/频率/白名单), 不硬编码
- [ ] **黑名单/白名单 + 频率限制**: 补全闸门(README 已承诺)
- [ ] **远程审批**: TG/Webhook 确认(参考 AirGuard alert TG 经验)
- [ ] 绑定 AirGuard: AI 修复人工批准用同款思路, 互相引流
- [ ] 多框架适配: OpenClaw/Claude Code/langchain 示例

## 竞品功能参考(02:03 拉取)
- preloop(55⭐): MCP firewall+model gateway+policy-as-code+human approvals+会话可观测+合规预设(SBOM/exploit)
- payment-guard(3⭐): 非托管确定性规则+双形态(MCP server+TS库); 提 Google AP2/Coinbase x402 标准
- black-vault(8⭐): API key 防火墙(代理层+per-token限额+一键kill)
- veronica-core(5⭐): 运行时约束内核

## 提升点(02:05 补充)
- [ ] 防绕过设计(确定性代码✅已有) + 密钥不落地(代理层) + 失败回滚
- [ ] 多框架示例(OpenClaw/Claude Code/langchain/autogen) + 文档站 + 规则模板库(电商/API/转账场景)
- [ ] 审计 Dashboard 可视化 + CLI 查看

## 🔑 身份与意图层(2026-08-31 16:07 行业对照后补, 最高优先级)

> 背景: 行业三层体系(监管 KYA / 平台 APOP·AP2·A2P2 / 技术 Sentinel·Fireblocks·Cloudflare)对照后, 我们现状=技术层拦截+审计子集, 缺身份/意图/密钥三根柱子 → 「闸门」还不是「栅栏」。

- [x] **Agent 身份层(KYA 最小实现, v0.4 完成)**: Agent ID + 策略按 ID 绑定(预算/黑名单/频率/approval 按 agent 分) + 未知默认拒绝 + 审计留痕 agent 字段 —— 记录「谁在花」
- [ ] **调用签名(身份层 v2)**: 每次调用带 agent 签名的 token, 防伪造身份(当前靠调用方自觉传 agent)
- [ ] **自动熔断(2026-08-31 20:57 外部建议吸收)**: 失败率异常飙升(如连续 N 次失败/短时失败率>阈值) → 自动暂停该 agent/全部支付, 需人工解除——对应失败回滚的升级版
- [ ] **状态化防御**: 上下文感知增强——检测 5 分钟内重复订单号/同金额同收款方重复调用等异常模式(现有频率限制的精细版)
- [ ] **策略变更审计**: YAML 策略文件变更记录 + 变更生效前走审批(策略即代码的配套纪律)
- [x] **意图一致性(v0.5 完成)**: 新收款方/大额强制审批(approve_new_recipient/approve_above), 未配审批通道默认拒绝, 交易记忆(known_recipients)——提示注入的务实解法
- [x] **密钥保险库(v0.6 完成)**: KeyVault(Fernet AES128-CBC+HMAC) 加密落盘, 主密钥环境变量不落盘, get_secret 过身份+意图闸门, 审计 secret_access; MCP secret_get 工具; 落盘无明文(有测试)
- [ ] **合规报告导出**: 对齐国内《智能体支付应用自律公约》(2026-08 发布) KYA + CRA/EU AI Act——国内 Agent 开发者迟早需要合规工具=变现点

## 未来布局(02:05 定案)
1. **标准跟随**: 盯 Google AP2 / Coinbase x402 —— **x402 适配层已落地 ✅(2026-08-31 22:5x)**: spendshield/adapters/x402.py(X402PaywallGuard 服务端 paywall 护栏 + protect_x402_payment 客户端支付前闸门 + confirm 累计) + 6 测试 + README x402 段; x402 官方仓库 6560★ / Python SDK v2.21; 下一个: AP2 落地时同样出适配层
2. **形态演进**: 库 → MCP server → 支付护栏网关/托管版(企业变现, preloop 模式: 开源+Cloud)
3. **场景绑定(独门)**: AirGuard 修复批准 / 麦当当自动化 / SparkData API 预算 — 竞品无落地场景, 我们边用边完善
4. **变现**: 开源免费引流 + 网关/企业版收费(CRA/EU AI Act 合规方向)
5. **时间线**: 现在完善v0.2 → 半年AP2落地发v1.0+故事文 → 1年agent支付爆发收割

## 发布时机
完善 1-4 后发（GitHub+PyPI），或等 AirGuard 发布时一起曝光。

## 前景判断(02:13 Felix 认同)
- **SpendShield = 低成本期权, 不是现金牛**
- 看多: AP2/x402 标准成形方向确定 / Python生态空白 / 99元事故背书 / 有落地场景
- 看空: 时间不确定(1-3年僵尸期) / **大厂内置风险(最大威胁)** / 竞争涌入窗口6-12月 / 变现周期长
- 姿势: 不 All-in(不追功能不运营), 低成本持有 + 绑定自己场景(AirGuard/麦当当) + AP2落地时发v1.0抢Python首选位
- **主线仍是 AirGuard**, SpendShield 当彩蛋养
