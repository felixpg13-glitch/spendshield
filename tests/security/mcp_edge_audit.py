#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP 边界对抗 — 坏路径测试: malformed / 并发 / 工具崩溃 / 重复调用。
目标: MCP 层任何畸形输入 → 安全错误, 不 crash 不误放行。
"""
import sys, os, json, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from spendshield import SpendShield
from spendshield.mcp_server import SpendShieldMCP

POLICY = {"version": "2.1.0",
          "policy": {"budget": {"daily": 100}, "transaction": {"max": 50},
                     "merchants": {"allowed": ["amazon.com"], "blocked": ["scam.com"]},
                     "approval": {"over": 30, "new_merchant": True, "channel": ""}}}

def make_mcp():
    import tempfile, yaml
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(POLICY, f); f.close()
    g = SpendShield(dry_run=False, log=lambda rec: None)
    g.load_policy(f.name); os.unlink(f.name)
    return SpendShieldMCP(g), g

issues = []
def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    print(f"{status} {name} {detail}")
    if not cond:
        issues.append(name)

m, g = make_mcp()

# 1) 正常路径必须 ALLOW(防误杀)
r = json.loads(m.tools_call("authorize_payment", {"recipient": "amazon.com", "amount": 25})["content"][0]["text"])
check("正常 $25 → ALLOW", r["status"] == "ALLOW", f"({r['status']})")

# 2) 畸形输入矩阵(工具层)
bad_args = [
    {}, {"amount": 75}, {"recipient": "amazon.com"}, {"recipient": "amazon.com", "amount": "abc"},
    {"recipient": "amazon.com", "amount": -5}, {"recipient": "amazon.com", "amount": 0},
    {"recipient": "amazon.com", "amount": None}, {"recipient": None, "amount": 75},
    {"recipient": {"x": 1}, "amount": 75}, {"amount": float("nan"), "recipient": "amazon.com"},
    {"recipient": "amazon.com", "amount": 75, "agent_id": {"evil": 1}},
    {"recipient": "amazon.com", "amount": 75, "purpose": {"inject": "x"}},
    {"recipient": "amazon.com", "amount": 75, "meta": "not-a-dict"},
    {"recipient": "scam.com", "amount": 75},  # 黑名单
]
for i, args in enumerate(bad_args):
    try:
        r = m.tools_call("authorize_payment", args)
        body = json.loads(r["content"][0]["text"])
        status = body.get("status")
        check(f"畸形输入 {i} → 非ALLOW", status in ("DENY", "ERROR", "APPROVAL"), f"({status})")
    except Exception as e:
        check(f"畸形输入 {i} 不crash", False, f"CRASH: {str(e)[:60]}")

# 3) 未知工具名 → 安全错误
try:
    r = m.tools_call("nonexistent_tool", {})
    check("未知工具 → isError", r.get("isError", True), f"({r.get('isError')})")
except Exception as e:
    check("未知工具不crash", False, str(e)[:60])

# 4) 并发 100 线程混打(含畸形)
errors = []
def worker(i):
    try:
        args = {"recipient": "amazon.com", "amount": (25 if i % 2 else -1)}
        m.tools_call("authorize_payment", args)
    except Exception as e:
        errors.append(str(e)[:60])
threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
[t.start() for t in threads]; [t.join() for t in threads]
check("并发 100 无 crash", len(errors) == 0, f"({len(errors)} errors)")

# 5) 预算并发不破(20 笔 $25 并发 → 最多 4 笔 ALLOW, 预算 $100)
g2 = SpendShield(dry_run=False, log=lambda rec: None)
import tempfile, yaml
f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
yaml.safe_dump(POLICY, f); f.close()
g2.load_policy(f.name); os.unlink(f.name)
m2 = SpendShieldMCP(g2)
allows = []
def worker2():
    try:
        r = json.loads(m2.tools_call("authorize_payment", {"recipient": "amazon.com", "amount": 25})["content"][0]["text"])
        if r["status"] == "ALLOW":
            allows.append(1)
    except Exception:
        pass
threads = [threading.Thread(target=worker2) for _ in range(20)]
[t.start() for t in threads]; [t.join() for t in threads]
check("并发预算不破 (≤4 ALLOW)", len(allows) <= 4, f"({len(allows)} ALLOW / 20)")

# 6) 重放: 同 idempotency_key 两次 → 第二次必拒
r1 = json.loads(m.tools_call("authorize_payment", {"recipient": "amazon.com", "amount": 25, "meta": {"idempotency_key": "k1"}})["content"][0]["text"])
r2 = json.loads(m.tools_call("authorize_payment", {"recipient": "amazon.com", "amount": 25, "meta": {"idempotency_key": "k1"}})["content"][0]["text"])
check("重放第二次被拒", r2["status"] == "DENY", f"({r1['status']} → {r2['status']})")

print(f"\n{'='*50}\nMCP 边界测试: {'✅ 全过' if not issues else '❌ 有 ' + str(len(issues)) + ' 个问题'}")
sys.exit(0 if not issues else 1)
