# -*- coding: utf-8 -*-
"""SpendGuard MCP Server — AI Agent 直接调用付款护栏

让 Claude Code / OpenClaw / 任何 MCP 兼容 agent 通过工具调用过 SpendGuard 闸门。

协议: MCP stdio transport (newline-delimited JSON-RPC 2.0)
工具:
  - spend_protect: 保护一次花钱操作(走全部闸门)
  - spend_status:  查询预算/已花/策略状态
  - spend_audit:   最近审计记录
  - spend_reset:   重置会话已花金额

用法:
  python -m spendguard.mcp                 # 默认无策略
  python -m spendguard.mcp --policy xx.yaml
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .guard import SpendGuard, DryRunBlocked, BudgetExceeded, NeedsApproval

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "spendguard"
SERVER_VERSION = "0.2.0"


def _tool_schema(name: str, desc: str, props: dict, required: list) -> dict:
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props, "required": required}}


TOOLS = [
    _tool_schema("spend_protect", "保护一次花钱操作。走干跑/预算/黑名单/白名单/频率/单次上限闸门。"
                 "通过返回 ok=true; 被拦返回 ok=false + reason",
                 {"action": {"type": "string", "description": "操作名, 如 下单/转账/充值"},
                  "amount": {"type": "number", "description": "金额(元)"},
                  "to": {"type": "string", "description": "收款方, 如 麦当劳/xxx@example.com"}},
                 ["action", "amount", "to"]),
    _tool_schema("spend_status", "查询当前护栏状态: 预算/已花/剩余/拦截统计", {}, []),
    _tool_schema("spend_audit", "最近审计记录(最多 N 条)",
                 {"limit": {"type": "integer", "description": "条数, 默认 10"}}, []),
    _tool_schema("spend_reset", "重置本次会话已花金额(新会话/换预算时用)", {}, []),
]


class SpendGuardMCP:
    def __init__(self, guard: SpendGuard):
        self.guard = guard

    # ---------- MCP 方法 ----------
    def initialize(self, params: dict) -> dict:
        return {"protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}

    def tools_list(self) -> dict:
        return {"tools": TOOLS}

    def tools_call(self, name: str, arguments: dict) -> dict:
        try:
            result = self._dispatch(name, arguments or {})
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "isError": False}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"error: {str(e)[:200]}"}],
                    "isError": True}

    def _dispatch(self, name: str, args: dict):
        if name == "spend_protect":
            return self._protect(args)
        if name == "spend_status":
            return self.guard.summary()
        if name == "spend_audit":
            limit = int(args.get("limit", 10))
            return {"records": [r.to_dict() for r in self.guard.records[-limit:]]}
        if name == "spend_reset":
            self.guard._spent = 0.0
            return {"ok": True, "message": "已花金额已重置"}
        raise ValueError(f"未知工具: {name}")

    def _protect(self, args: dict) -> dict:
        action = args.get("action", "?")
        amount = float(args.get("amount", 0))
        to = args.get("to", "?")
        try:
            # 手动走闸门(不依赖装饰器): dry_run/黑名单/频率/预算/确认
            ok = self.guard._authorize(action, amount, to)
            if not ok:
                return {"ok": False, "reason": "审批被拒",
                        "spent": self.guard.spent}
            self.guard._spent += amount
            self.guard._record(action=action, amount=amount, to=to,
                               decision="executed", reason="mcp 调用通过",
                               spent_after=self.guard.spent)
            return {"ok": True, "approved": True, "amount": amount, "to": to,
                    "spent": self.guard.spent,
                    "note": "已放行。请在真实支付前调用你的支付接口"}
        except (DryRunBlocked, BudgetExceeded, NeedsApproval) as e:
            return {"ok": False, "reason": str(e), "spent": self.guard.spent}

    # ---------- JSON-RPC 分发 ----------
    def handle(self, line: str) -> str | None:
        """处理一行 JSON-RPC, 返回响应行(通知/错误返回 None)"""
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return None
        msg_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}

        if method == "initialize":
            result = self.initialize(params)
        elif method == "tools/list":
            result = self.tools_list()
        elif method == "tools/call":
            result = self.tools_call(params.get("name", ""), params.get("arguments") or {})
        elif method == "notifications/initialized":
            return None
        elif method == "ping":
            result = {}
        else:
            # 未知方法: 返回错误
            if msg_id is not None:
                return json.dumps({"jsonrpc": "2.0", "id": msg_id,
                                   "error": {"code": -32601, "message": f"method not found: {method}"}})
            return None
        if msg_id is None:
            return None
        return json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser(prog="spendguard-mcp")
    ap.add_argument("--policy", default=os.environ.get("SPENDGUARD_POLICY", ""),
                    help="策略文件路径(spendguard.yaml)")
    ap.add_argument("--budget", type=float, default=0.0)
    ap.add_argument("--dry-run", action="store_true", default=True, help="干跑模式(默认开)")
    ap.add_argument("--no-dry-run", action="store_true", help="关闭干跑")
    args = ap.parse_args()

    guard = SpendGuard(budget=args.budget, dry_run=not args.no_dry_run,
                       log=lambda rec: print(f"[SpendGuard] {rec.decision}: {rec.action} \u00a5{rec.amount} -> {rec.to}", file=sys.stderr))
    if args.policy:
        guard.load_policy(args.policy)
    mcp = SpendGuardMCP(guard)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        resp = mcp.handle(line)
        if resp:
            sys.stdout.write(resp + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
