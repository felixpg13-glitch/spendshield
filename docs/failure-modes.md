# SpendShield Failure Mode Library(2026-09-05 起, 持续追加)

> 规则(Felix 定, 资产 #4): 每个真实坑/攻击 = 一条记录: **Attack/Failure → Expected behavior → SpendShield decision → Proof**。
> 与 attack-corpus.md(内部红队语料, 编号 AUTH/REPLAY/RACE/POLICY/INPUT/MCP/CRED/MIG)互补: 这里 = 面向外部的可讲案例。
> 场景编号 F1-F6(跨资产统一引用, 冻结不改)。

## 结构化失败模式(带 proof)

### F1 — Retry storm(重试风暴)
- **Failure**: 失败请求无限重试, 每笔在 cap 内 → 累计爆预算
- **Expected**: 序列级限制(累计窗口), 不是单笔判断
- **SpendShield decision**: DENY(超出 daily budget window; 窗口在 agent 循环外原子累计)
- **Proof**: `tests/security/test_budget_bypass.py` + e2e shot3(replay/benefit used)

### F2 — Parallel execution(并发竞态)
- **Failure**: 多 agent/多请求同时读「预算剩余」全通过 → 超支
- **Expected**: reserve-and-commit 原子扣账
- **SpendShield decision**: 决策+记账同一步(锁内, 防 TOCTOU)
- **Proof**: `tests/security/test_race_condition.py` + guard.py authorize 锁内记账

### F3 — Transaction splitting(拆单)
- **Failure**: $200 → 5×$40 绕过单笔 cap
- **Expected**: cap 只约束单笔; 累计窗口约束总额
- **SpendShield decision**: DENY(预算窗口在拆分中照常累计, 撞窗即拒)
- **Proof**: `tests/security/test_budget_bypass.py`(拆分用例)+ playground F3 场景

### F4 — Wrong recipient(错收款方)
- **Failure**: 金额合法, 收款方错(typo/篡改/新地址)
- **Expected**: recipient policy + 新收款方人审
- **SpendShield decision**: DENY(blocked/不在 allow 列表); 新收款方 → APPROVAL 或默认拒
- **Proof**: `tests/security/test_parameter_tampering.py` + agentkit demo $500 blocked → DENY(executions 0)

### F5 — Prompt injection(恶意指令)
- **Failure**: 模型「认为」该付(邮件/工具结果注入)
- **Expected**: 决策来自确定性代码, 不来自模型; 新收款方/大额走人
- **SpendShield decision**: DENY/APPROVAL(结构化 reason, 模型无法争辩)
- **Proof**: `tests/security/test_approval_bypass.py` + e2e shot2($500 scam injection → DENY)

### F6 — Approval replay(审批重放)
- **Failure**: 一次 human yes 被执行两次(retry/双 agent/crash 恢复)
- **Expected**: approval 绑定具体 request + 一次消费
- **SpendShield decision**: 签名一次性 grant; 同一 grant 再验 → REFUSED(REUSED)
- **Proof**: agentkit demo(replay same grant → REFUSED, executions 0); `tests/security/test_replay_attack.py` + test_double_spend.py

## 引擎级失败模式(带 proof)
| 案例 | Attack/Failure | Expected | Decision | Proof |
|---|---|---|---|---|
| FM-01 | 伪造签名 grant(attacker key) | fail-closed | DENY(INVALID_SIGNATURE) | execution_gateway_demo case3 |
| FM-02 | 篡改真 grant($25→$5000) | 字段绑定 | DENY(INVALID_SIGNATURE/字段不符) | execution_gateway_demo case4 |
| FM-03 | 畸形 token | 不抛异常, 结构化拒 | DENY(MALFORMED) | enforce.py verify + tests |
| FM-04 | bool/NaN/负数金额 | 入口拒畸形 | DENY(invalid amount) | guard.py authorize + adversarial_10k |
| FM-05 | 空/未注册 agent | 身份闸门 | DENY(unknown agent) | guard.py + VA composition |
| FM-06 | 审批期策略被改(宽松窗→收紧) | 审批时重评估 | DENY(policy tampered/重查) | guard.py approve 防篡改 + test_approval_bypass |
| FM-07 | 预算耗尽后仍请求 | 硬上限 | DENY(budget) | e2e shot3 + test_budget_bypass |
| FM-08 | 审批通道未配 | 安全默认 | DENY(no channel) | guard.py + test_approval_bypass |

## 引用方式(外部可讨论)
- 场景编号: Playground F3 = README example F3 = Integration test F3 = Failure Map F3
- 讨论素材: 「capability exists but authority absent」「stale authorization」「reused payment」→ 直接引用 F 编号/FM 编号
