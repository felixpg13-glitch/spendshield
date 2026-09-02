# Verb Authority beta.14 扫描 SpendShield MCP schemas(2026-09-02)

> 工具: verb-authority==0.10.0b14(离线, 无网络)
> 输入: spendshield/mcp_server.py 导出的真实 17 个 MCP 工具(36 参数)
> 原始报告: report-v0100b14.json

## 摘要
- tools: 17 | parameters: 36 | protected: 36/36
- confirmation_required_tools: 17 | review_required: 32 | risk_review_required_tools: 17
- schema_review_required_tools: 1(policy_sim)
- annotation_conflicts: 0 | risk_conflicts: 0

## 高置信分类(无需人工复查)
- `to` / `recipient`(spend_protect / spend_authorize / authorize_payment / policy_sim): policy=trusted_fixed, conf=high, 理由=authority-bearing name
  → 与我们的字段绑定设计一致: recipient/merchant 是 authority 载体, 必须 trusted 来源

## 低置信(需 review): amount / purpose / agent / approval_id / policy 内容 / by
- 全部 trusted_fixed 但 conf=uncertain → VA 从 schema 无法判断"金额该由应用提供还是模型提议"
- 我们的 schema 未声明 authority 意图(无 control annotations)

## 对我们的设计信号
1. VA 默认把所有资金相关参数当 trusted_fixed → 与我们的 provenance 缺口判断一致:
   MCP advisory demo 里 agent 自拟 amount/recipient = 该补 trusted_args 通道的环节
2. enforcement 模式(0.8.2, enforce.py)天然匹配: grant 签发时 amount/recipient 绑定应用状态
   → VA runtime(来源强制)+ SpendShield(策略决策+签名)可组合
3. 建议(可选): 给 MCP schemas 加 VA control declarations(amount=from_application, purpose=model_writable)
