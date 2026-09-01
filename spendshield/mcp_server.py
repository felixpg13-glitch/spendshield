# -*- coding: utf-8 -*-
"""SpendShield MCP Server — AI Agent 直接调用付款护栏

让 Claude Code / OpenClaw / 任何 MCP 兼容 agent 通过工具调用过 SpendShield 闸门。

协议: MCP stdio transport (newline-delimited JSON-RPC 2.0)
工具:
  - spend_protect: 保护一次花钱操作(走全部闸门)
  - spend_status:  查询预算/已花/策略状态
  - spend_audit:   最近审计记录
  - spend_reset:   重置会话已花金额

用法:
  python -m spendshield.mcp                 # 默认无策略
  python -m spendshield.mcp --policy xx.yaml
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .guard import SpendShield, DryRunBlocked, BudgetExceeded, NeedsApproval, UnknownAgent

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "spendshield"
SERVER_VERSION = "0.3.0"


def _tool_schema(name: str, desc: str, props: dict, required: list) -> dict:
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props, "required": required}}


TOOLS = [
    _tool_schema("spend_protect", "保护一次花钱操作。走干跑/预算/黑名单/白名单/频率/单次上限闸门。"
                 "通过返回 ok=true; 被拦返回 ok=false + reason",
                 {"action": {"type": "string", "description": "操作名, 如 下单/转账/充值"},
                  "amount": {"type": "number", "description": "金额(元)"},
                  "to": {"type": "string", "description": "收款方, 如 麦当劳/xxx@example.com"},
                  "agent": {"type": "string", "description": "调用方 Agent 身份 ID(未注册默认拒绝, 建议必填)"}},
                 ["action", "amount", "to"]),
    _tool_schema("spend_status", "查询当前护栏状态: 预算/已花/剩余/拦截统计", {}, []),
    _tool_schema("spend_audit", "最近审计记录(最多 N 条)",
                 {"limit": {"type": "integer", "description": "条数, 默认 10"}}, []),
    _tool_schema("spend_reset", "重置本次会话已花金额(新会话/换预算时用)", {}, []),
    _tool_schema("secret_get", "从密钥保险库取密钥(过身份+审批闸门, 留审计)。密钥名视为收款方, 可加白名单免问",
                 {"name": {"type": "string", "description": "密钥名(如 mcd_sk)"},
                  "agent": {"type": "string", "description": "调用方 Agent 身份 ID"}},
                 ["name", "agent"]),
    _tool_schema("spend_authorize", "V2 授权入口: 返回三态决策 ALLOW/DENY/APPROVAL + 命中规则(RuleHit 结构化)。"
                 "ALLOW=可付款; APPROVAL=需人工审批(拿 approval_id 调 spend_approve); DENY=拒绝",
                 {"agent": {"type": "string", "description": "Agent 身份 ID(匿名可省)"},
                  "amount": {"type": "number", "description": "金额"},
                  "to": {"type": "string", "description": "收款方"},
                  "meta": {"type": "object", "description": "附加上下文(可带 idempotency_key 防重放)"}},
                 ["amount", "to"]),
    _tool_schema("spend_approve", "批准一笔挂起审批(APPROVAL 状态)。批准后重新评估, 返回最终决策",
                 {"approval_id": {"type": "string", "description": "spend_authorize 返回的 approval_id"},
                  "by": {"type": "string", "description": "批准人标识"}},
                 ["approval_id"]),
    _tool_schema("spend_reject", "拒绝一笔挂起审批",
                 {"approval_id": {"type": "string", "description": "spend_authorize 返回的 approval_id"},
                  "by": {"type": "string", "description": "拒绝人标识"}},
                 ["approval_id"]),
    _tool_schema("policy_sim", "花钱前模拟(不真花): 评估单笔或金额扫描, 返回决策+命中规则。"
                 "Agent 在发起支付前先问'这单会不会被拦'",
                 {"agent": {"type": "string", "description": "Agent 身份 ID"},
                  "amount": {"type": "number", "description": "单笔金额(与 amounts 二选一)"},
                  "amounts": {"type": "array", "items": {"type": "number"}, "description": "金额扫描列表(找边界)"},
                  "to": {"type": "string", "description": "收款方"}},
                 ["to"]),
    _tool_schema("policy_apply", "应用/更新策略(宿主管理工具, 仅限受信调用者)。传 policy JSON 生效新规则;"
                 "无参数返回当前策略摘要。策略变更会审计留痕",
                 {"policy": {"type": "string", "description": "V2 policy JSON(可选, 省略=查当前)"}},
                 []),
]


class SpendShieldMCP:
    def __init__(self, guard: SpendShield):
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
            return self.guard.status()   # V2 全量状态(引擎版本/策略版本/预算/审批/频率)
        if name == "spend_audit":
            limit = int(args.get("limit", 10))
            return {"records": [r.to_dict() for r in self.guard.records[-limit:]]}
        if name == "spend_reset":
            self.guard.reset()
            self.guard._record(action="spend_reset", amount=0, to="",
                               decision="spend_reset", reason="会话状态完整重置",
                               spent_after=self.guard.spent)
            return {"ok": True, "message": "会话状态已完整重置(预算/频率/审批/收款方记忆)"}
        if name == "secret_get":
            return self._secret_get(args)
        if name == "spend_authorize":
            return self._authorize_v2(args)
        if name == "spend_approve":
            return self._approve_v2(args)
        if name == "spend_reject":
            return self._reject_v2(args)
        if name == "policy_sim":
            return self._policy_sim(args)
        if name == "policy_apply":
            return self._policy_apply(args)
        raise ValueError(f"未知工具: {name}")

    def _protect(self, args: dict) -> dict:
        action = args.get("action", "?")
        amount = float(args.get("amount", 0))
        to = args.get("to", "?")
        agent = args.get("agent", "")
        try:
            # V2 授权评估(book=False, 执行后由 book 记账, 与 V2 state 同步)
            res = self.guard.authorize(agent, amount, to, action=action, book=False)
            if res.decision == "DENY":
                return {"ok": False, "decision": "DENY", "reason": res.reason,
                        "spent": self.guard.spent}
            if res.decision == "APPROVAL":
                return {"ok": False, "decision": "APPROVAL",
                        "reason": res.reason, "approval_id": res.approval_id,
                        "hint": f"需审批: 调 spend_approve approval_id={res.approval_id}",
                        "spent": self.guard.spent}
            # ALLOW → 执行后记账(同步 V2 state + 旧层)
            self.guard.book(agent=agent, amount=amount, to=to)
            self.guard._record(action=action, amount=amount, to=to, agent=agent,
                               decision="executed", reason="mcp 调用通过",
                               spent_after=self.guard.spent)
            return {"ok": True, "approved": True, "amount": amount, "to": to,
                    "spent": self.guard.spent,
                    "note": "已放行。请在真实支付前调用你的支付接口"}
        except (DryRunBlocked, BudgetExceeded, NeedsApproval, UnknownAgent) as e:
            return {"ok": False, "reason": str(e), "spent": self.guard.spent}

    def _secret_get(self, args: dict) -> dict:
        name = args.get("name", "")
        agent = args.get("agent", "")
        if self.guard.vault is None:
            return {"ok": False, "reason": "未配置 KeyVault(需设置 SPENDGUARD_MASTER_KEY + vault 路径)"}
        try:
            secret = self.guard.get_secret(name, agent=agent)
            return {"ok": True, "name": name, "secret": secret}
        except (DryRunBlocked, BudgetExceeded, NeedsApproval, UnknownAgent, KeyError) as e:
            return {"ok": False, "reason": str(e)}

    def _authorize_v2(self, args: dict) -> dict:
        agent = args.get("agent", "")
        to = args.get("to", "?")
        meta = args.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        try:
            amount = float(args.get("amount", 0))
        except (TypeError, ValueError):
            return {"ok": False, "decision": "ERROR", "reason": "amount must be a number"}
        try:
            r = self.guard.authorize(agent, amount, to, meta=meta, action="mcp:authorize")
            return {"ok": r.decision == "ALLOW", "decision": r.decision,
                    "reason": r.reason,
                    "rules": [h.to_dict() for h in r.rules],
                    "approval_id": r.approval_id, "spent": self.guard.spent,
                    "hint": ("已授权可付款" if r.decision == "ALLOW"
                             else f"需审批: 调 spend_approve approval_id={r.approval_id}"
                             if r.decision == "APPROVAL" else "被拒, 见 reason")}
        except Exception as e:
            return {"ok": False, "decision": "ERROR", "reason": str(e)[:200]}

    def _approve_v2(self, args: dict) -> dict:
        aid = args.get("approval_id", "")
        r = self.guard.approve(aid, by=args.get("by", "mcp"))
        return {"ok": r.decision == "ALLOW", "decision": r.decision, "reason": r.reason}

    def _reject_v2(self, args: dict) -> dict:
        aid = args.get("approval_id", "")
        r = self.guard.reject(aid, by=args.get("by", "mcp"))
        return {"ok": False, "decision": r.decision, "reason": r.reason}

    def _policy_sim(self, args: dict) -> dict:
        from .policy import PolicySimulator
        agent = args.get("agent", "")
        to = args.get("to", "?")
        amounts = args.get("amounts")
        if amounts is None and args.get("amount") is not None:
            amounts = [float(args["amount"])]
        if not amounts:
            return {"ok": False, "reason": "需要 amount 或 amounts"}
        try:
            sim = PolicySimulator(policy=self.guard._v2_policy, agents=self.guard._v2_agents)
            results = {}
            for amt in amounts:
                r = sim.evaluate(agent, float(amt), to)
                results[str(amt)] = {"decision": r.decision, "reason": r.reason,
                                     "rules": [h.to_dict() for h in r.rules]}
            return {"ok": True, "results": results,
                    "note": "纯模拟, 未记账未付款"}
        except Exception as e:
            return {"ok": False, "reason": str(e)[:200]}

    def _policy_apply(self, args: dict) -> dict:
        content = args.get("policy")
        p = self.guard._v2_policy
        if not content:
            if p is None:
                return {"ok": False, "reason": "no policy loaded"}
            import dataclasses
            return {"ok": True, "version": p.version,
                    "policy": dataclasses.asdict(p)}
        # 应用新策略(宿主管理操作, 审计留痕)
        try:
            raw = json.loads(content) if isinstance(content, str) else content
            if not isinstance(raw, dict):
                return {"ok": False, "reason": "policy must be a JSON object"}
            if len(json.dumps(raw)) > 1_000_000:
                return {"ok": False, "reason": "policy too large"}
        except json.JSONDecodeError as e:
            return {"ok": False, "reason": f"invalid JSON: {str(e)[:100]}"}
        import tempfile, os
        import yaml
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.safe_dump(raw, f)
            path = f.name
        try:
            old_version = p.version if p else "(none)"
            self.guard.load_policy(path)
            self.guard._record(action="policy_apply", amount=0, to="",
                               decision="policy_applied",
                               reason=f"{old_version} -> {raw.get('version', '?')}",
                               spent_after=self.guard.spent)
            self.guard.audit.append(
                request_id="", actor="mcp:policy_apply", action="policy_apply",
                decision="APPLIED", reason_codes=[], primary_reason=f"{old_version} -> {raw.get('version', '?')}",
                amount=0.0, currency="", merchant="", approval_state="none",
                input_hash="", meta={}, policy_version=str(raw.get("version", "?")),
                policy_hash=self.guard._policy_fp(),
                engine_version=__import__("spendshield", fromlist=["__version__"]).__version__)
            return {"ok": True, "version": raw.get("version", "?"),
                    "message": f"policy applied ({old_version} -> {raw.get('version', '?')})"}
        except Exception as e:
            return {"ok": False, "reason": f"policy rejected: {str(e)[:200]}"}
        finally:
            os.unlink(path)

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
    ap = argparse.ArgumentParser(prog="spendshield-mcp")
    ap.add_argument("--policy", default=os.environ.get("SPENDGUARD_POLICY", ""),
                    help="策略文件路径(spendshield.yaml)")
    ap.add_argument("--budget", type=float, default=0.0)
    ap.add_argument("--dry-run", action="store_true", default=True, help="干跑模式(默认开)")
    ap.add_argument("--no-dry-run", action="store_true", help="关闭干跑")
    args = ap.parse_args()

    guard = SpendShield(budget=args.budget, dry_run=not args.no_dry_run,
                       log=lambda rec: print(f"[SpendShield] {rec.decision}: {rec.action} \u00a5{rec.amount} -> {rec.to}", file=sys.stderr))
    if args.policy:
        guard.load_policy(args.policy)
    mcp = SpendShieldMCP(guard)

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
