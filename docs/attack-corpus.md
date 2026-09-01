# SpendShield Attack Corpus — 攻击语料库

> 每个攻击: 编号 / 分类 / 最小复现 / 期望安全属性 / 回归测试位置。
> **这是长期壁垒的核心资产**: 功能别人能抄, 攻击语料 + 回归体系抄不走。
> 规则: 每发现一个新攻击 → 编号入库 → 最小复现 → 期望不变量 → 永久回归测试。

## 分类体系

| 前缀 | 类别 | 含义 |
|---|---|---|
| AUTH | 授权绕过 | 绕过/篡改授权流程 |
| REPLAY | 重放 | 同一交易重复生效 |
| RACE | 并发竞态 | 并发突破预算/状态 |
| POLICY | 策略绕过 | 规则匹配绕过/优先级混乱 |
| INPUT | 恶意输入 | 畸形/类型混淆/数值攻击 |
| MCP | MCP 滥用 | 工具参数/工具链组合攻击 |
| CRED | 凭证泄漏 | 敏感信息外泄 |
| MIG | 迁移 | 配置迁移丢失语义 |

---

## AUTH — 授权绕过

### AUTH-001 策略变更后旧审批残留(严重 🔴)
- **攻击**: 宽松策略(max=100)挂起 APPROVAL → 策略收紧到 max=10 → 旧审批仍批准放行 + 记账 40
- **最小复现**: 挂起 40 审批 → `_setup_v2(收紧策略)` → `approve(旧 approval_id)` → 必须 DENY
- **期望不变量**: `APPROVAL_REQUIRED → CANNOT_DIRECTLY_BECOME_APPROVED`; 策略变更 = 旧审批作废
- **修复**: ①_load_policy 清空 pending ②approve 用当前策略重新 merge + 指纹校验
- **回归**: `tests/security/test_security_audit.py::test_stale_approval_invalid_after_policy_change`

### AUTH-002 审批金额篡改绕过
- **攻击**: 挂起 40 审批后, 把 pending 金额改成 9999 → 批准 → 必须 DENY(记账≠篡改值)
- **期望不变量**: `authorized_amount == evaluated_amount`
- **回归**: `test_invariants.py::test_invariant_approval_binds_original_request`

### AUTH-003 双重批准 double spend
- **攻击**: 同一 approval_id approve 两次 → 第二次必须 DENY, 只记账一次
- **回归**: `test_double_spend.py::test_approve_twice_denied`

### AUTH-004 状态机跳跃(reject→approve)
- **攻击**: reject 后再 approve 同一 id → 必须 DENY(状态单向不可逆)
- **回归**: `test_hardening_invariants.py::test_reject_then_approve_denied`

### AUTH-005 无效 approval_id
- **攻击**: approve(""/None/不存在/超长) → 必须 DENY
- **回归**: `test_hardening_invariants.py::test_invalid_approval_id_never_succeeds`

### AUTH-006 策略篡改时审批
- **攻击**: 运行时改 policy 对象(调大 max) → 审批必须 DENY "policy tampered"
- **回归**: `test_hardening_invariants.py::test_approve_with_tampered_policy_denied`

### AUTH-007 未知/空身份(身份退化)
- **攻击**: 非空未注册 agent → DENY(安全默认); 空 agent = 匿名走全局(合法)
- **回归**: `test_policy_bypass.py::test_unknown_agent_denied`

---

## REPLAY — 重放

### REPLAY-001 同 key 重放
- **攻击**: `idempotency_key="order-001"` 成功后再提交同 key → DENY, 不二次记账
- **期望不变量**: `REPLAYED_REQUEST ≠ NEW_AUTHORIZATION`
- **回归**: `test_replay_attack.py::test_replay_same_key_denied`

### REPLAY-002 同 key 变体篡改
- **攻击**: 同 key 改金额(50→80) → 必须 DENY(key 存在即拦)
- **回归**: `test_replay_attack.py::test_replay_different_amount_same_key_denied`

### REPLAY-003 审批后重放
- **攻击**: APPROVAL 批准后同 key 重放 → DENY(approve 也记 replay)
- **回归**: `test_replay_attack.py::test_replay_after_approval_denied`

### REPLAY-004 跨 agent key 冲突(已修复)
- **攻击**: agent A 用 k1 会误伤 agent B 的 k1 → key 加 agent 前缀
- **回归**: `test_security_audit.py::test_replay_key_isolated_per_agent`

---

## RACE — 并发竞态

### RACE-001 并发预算突破
- **攻击**: 预算 100, 并发两笔 60 → 只过 1 笔, 总额 ≤ 100
- **期望不变量**: `spent <= budget` 永真(评估-记账在锁内原子)
- **回归**: `test_budget_bypass.py::test_concurrent_budget_bypass`

### RACE-002 并发批准同一单
- **攻击**: 5 线程 approve 同一 approval_id → 只成功 1 次, 记账 1 次
- **回归**: `test_race_condition.py::test_concurrent_approve_no_duplicate_booking`

### RACE-003 并发打满 rate 窗口
- **攻击**: 20 线程并发 → 窗口 max_calls=5 只放 5
- **回归**: `test_race_condition.py::test_rate_limit_window_race`

### RACE-004 load_policy 与 authorize 竞态
- **攻击**: 并发 reload policy + authorize → 不 crash, 不出现半配置状态
- **回归**: `test_security_audit2.py::test_policy_reload_while_authorizing`

---

## POLICY — 策略绕过

### POLICY-001 白名单子串绕过
- **攻击**: `notamazon.com` 含 "amazon.com" 子串 → 命中白名单 → 必须 DENY
- **期望不变量**: 白名单 = 精确域匹配(nm==a 或 endswith(".a"))
- **回归**: `test_parameter_tampering.py::test_suffix_spoof_denied`

### POLICY-002 NaN/Inf 金额漏过
- **攻击**: `float("nan")` 所有比较 False → 骗过全部闸门 → 必须 DENY(isfinite 前置拦截)
- **回归**: `test_policy_bypass.py::test_nan_inf_amount_denied`

### POLICY-003 空商户命中白名单
- **攻击**: `"" in "amazon.com" = True` → 空收款方命中 → 必须 DENY
- **回归**: `test_policy_bypass.py::test_empty_merchant_denied`

### POLICY-004 allow_subdomains=False 仍放行子域
- **攻击**: 关闭子域信任后 `checkout.amazon.com` 仍被 endswith 放行 → 收紧条件
- **回归**: `test_parameter_tampering.py::test_subdomain_spoof_with_subdomains_disabled`

### POLICY-005 黑名单合并语义
- **攻击**: agent 配置空 blocked 覆盖全局黑名单 → 黑名单必须合并(只更严)
- **回归**: `test_dual_run.py`(迁移等价)+ `test_hardening_engine_edges.py::test_agent_blocked_merges_with_global`

### POLICY-006 信任前缀子串绕过
- **攻击**: whitelist 迁移的 "amazon.com" 信任项 → "notamazon.com" 免审批 → 域边界匹配
- **回归**: `test_security_audit2.py::test_trusted_domain_boundary`

### POLICY-007 agents 配置篡改
- **攻击**: 改 `_v2_agents` 限额 → 指纹检测 DENY(指纹覆盖 policy+agents)
- **回归**: `test_security_audit.py::test_agents_tamper_detected`

### POLICY-008 黑名单 ⊳ 白名单冲突
- **攻击**: 同一商户双名单 → deny 优先
- **回归**: `test_hardening_engine_edges.py::test_blocklist_wins_over_allowlist`

---

## INPUT — 恶意输入

### INPUT-001 负金额 / 0 金额
- **期望不变量**: `INVALID_AMOUNT ≠ APPROVED`; amount <= 0 → DENY
- **回归**: `test_policy_bypass.py::test_negative_amount_denied` / `test_zero_amount_denied`

### INPUT-002 类型混淆
- **攻击**: amount="abc" / dict / list / None → 绝不 APPROVED(引擎层 fail-closed, MCP 层安全 ERROR)
- **回归**: `test_hardening_invariants.py::test_malformed_requests_never_approved` / `test_hardening_mcp.py::test_authorize_malicious_params`

### INPUT-003 meta 非 dict
- **攻击**: meta="not-a-dict" → 规范化为 {} 不 crash
- **回归**: `test_hardening_mcp.py::test_authorize_malicious_params`

### INPUT-004 超大金额
- **攻击**: 1e18 / 1e309 → DENY(预算/isfinite 拦截)
- **回归**: `test_policy_bypass.py::test_huge_amount_denied`

### INPUT-005 次正规金额(已知限制)
- **攻击**: 1e-320 刷单 → 无资金危害, 记录为已知限制(README Threat Model)

---

## MCP — MCP 滥用

### MCP-001 float 解析 crash(已修复)
- **攻击**: `spend_authorize` amount="abc" → MCP 层直接抛 ValueError → 修成安全 ERROR 返回
- **回归**: `test_hardening_mcp.py::test_authorize_malicious_params`

### MCP-002 非法 JSON policy
- **攻击**: `policy_apply` 传 "not json" → 安全拒绝, 原策略不破坏
- **回归**: `test_hardening_mcp.py::test_policy_apply_malicious_payloads`

### MCP-003 超大连锁
- **攻击**: policy 超大(>1MB)→ 拒绝; 类型错误(channel=123)→ 拒绝
- **回归**: `test_hardening_mcp.py::test_policy_apply_malicious_payloads`

### MCP-004 工具链组合
- **攻击**: policy_apply 放宽 → spend_authorize 大额 → 允许但审计留痕(policy_applied + v2_allow)
- **回归**: `test_hardening_mcp.py::test_policy_apply_loosen_then_spend_is_audited`

### MCP-005 by 参数注入
- **攻击**: approve by="attacker\n[SpendShield] executed..." → 注入不能多记账
- **回归**: `test_hardening_mcp.py::test_by_injection_sanitized_or_harmless`

---

## CRED — 凭证泄漏

### CRED-001 meta 明文泄漏(已修复)
- **攻击**: `pending_approvals()` 返回完整 meta → password 明文 → 输出统一脱敏
- **回归**: `test_security_audit3.py::test_pending_approvals_redacted`

### CRED-002 审计不含 meta
- **攻击**: AuditRecord 不落 meta → 无泄漏面
- **回归**: `test_credential_leak.py::test_audit_records_no_meta_at_all`

---

## MIG — 迁移

### MIG-001 迁移静默失败(严重 🔴)
- **攻击**: `_migrate_v1` staticmethod 里用 self → 迁移被 except 静默吞掉 → 全局黑名单失效(隐藏 2 轮)
- **教训**: 转换代码异常绝不静默吞
- **回归**: `test_dual_run.py::test_migration_equivalent_decisions`

### MIG-002 迁移等价性
- **攻击**: 随机 V1 配置 ×50 组迁移 → 不 crash, 黑名单/预算/agent 意图保留
- **回归**: `test_hardening_migration.py`(property-based)

---

## 统计

| 分类 | 案例数 | 严重 |
|---|---|---|
| AUTH | 7 | 1 严重 |
| REPLAY | 4 | - |
| RACE | 4 | - |
| POLICY | 8 | - |
| INPUT | 5 | - |
| MCP | 5 | - |
| CRED | 2 | 1 泄漏 |
| MIG | 2 | 1 严重 |
| **合计** | **37** | **3 严重级** |

> 规则: 新攻击 → 在此登记编号 → 最小复现 → 期望不变量 → 回归测试 → 永久保留。
> 每版本 5 问: ①新增能力 ②新增攻击面 ③新增不变量 ④新增 Regression Cases ⑤旧测试保持全绿?
