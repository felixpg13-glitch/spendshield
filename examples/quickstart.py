#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SpendShield 60 秒体验 — 先看它拦住钱, 再研究它怎么做到的。

运行:
    pip install spendshield
    python examples/quickstart.py

预期输出:
    ❌ DENY   — transaction $75.00 exceeds the $50.00 limit
    ✅ ALLOW  — approved (within limits)
"""
from spendshield import SpendShield

# 一行初始化: 预算 $100/天, 单笔上限 $50
shield = SpendShield(budget=100, max_amount=50, dry_run=False,
                     approve_new_recipient=False)

print("SpendShield — 让 AI Agent 花钱前先过闸门\n")

# 场景 1: Agent 想花 $75(超单笔上限 $50)
r1 = shield.authorize("", 75, "amazon.com")
print(f"{'❌' if r1.decision == 'DENY' else '✅'} {r1.decision:9s} — {r1.reason}")

# 场景 2: Agent 花 $25(在限额内)
r2 = shield.authorize("", 25, "amazon.com")
print(f"{'❌' if r2.decision == 'DENY' else '✅'} {r2.decision:9s} — {r2.reason}")

print("""
它就是这么简单:
  1. 一行初始化(预算/单笔上限)
  2. 每次花钱前调 authorize()
  3. 拿到 ALLOW / APPROVAL(需人工) / DENY + 原因

想更深入? policy.yaml 可以定义: 商户白名单、审批阈值、
频率限制、日/月预算... 全部可验证、可审计、可回滚。
""")
