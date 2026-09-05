#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SpendShield × Coinbase AgentKit — policy authorization BEFORE AgentKit payment actions.

The point this example makes:

    Agent decides to transfer
        -> SpendShield.authorize()          (policy: budget / cap / merchant list / approval)
        -> ALLOW   : signed one-time grant issued
        -> APPROVAL: human approves -> grant issued
        -> DENY    : the AgentKit payment action is NEVER invoked
        -> REUSED  : an already-consumed grant is refused (one-time)

Every scenario proves the boundary by counting how many times the AgentKit
wallet's native_transfer actually executed (the true execution point under
Action.invoke). No real chain, no real money — deterministic demo.

Run:
    pip install coinbase-agentkit spendshield
    python examples/integration/agentkit/run_demo.py
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

# ── coinbase-agentkit 0.7.x imports solana.rpc.api, which solana>=0.37 removed ──
# This demo never instantiates the Solana wallet provider, so stub the module if
# the installed solana is too new. (Remove once upstream fixes the import.)
try:
    import solana.rpc.api  # noqa: F401
except ImportError:
    import sys as _sys
    import types as _types
    _sol = _types.ModuleType("solana"); _sol.__path__ = []
    _rpc = _types.ModuleType("solana.rpc"); _rpc.__path__ = []
    _api = _types.ModuleType("solana.rpc.api")
    _api.Client = type("Client", (), {})
    _sys.modules["solana"] = _sol
    _sys.modules["solana.rpc"] = _rpc
    _sys.modules["solana.rpc.api"] = _api

# ── quiet AgentKit telemetry (demo env has no Coinbase keys) ──────────────
import coinbase_agentkit.wallet_providers.wallet_provider as _wp
import coinbase_agentkit.action_providers.action_provider as _ap
import coinbase_agentkit.action_providers.action_decorator as _ad
for _m in (_wp, _ap, _ad):
    if hasattr(_m, "send_analytics_event"):
        _m.send_analytics_event = lambda *a, **k: None

from coinbase_agentkit.action_providers.wallet.wallet_action_provider import WalletActionProvider  # noqa: E402
from coinbase_agentkit.network import Network  # noqa: E402
from coinbase_agentkit.wallet_providers.wallet_provider import WalletProvider  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)  # use the repo's spendshield engine (matches this checkout)

from spendshield import SpendShield  # noqa: E402
from spendshield.enforce import AuthorizationIssuer, Executor  # noqa: E402

AGENT = "demo-agent"
SECRET = "agentkit-demo-secret-change-me"

ALLOWED_ADDR = "0x1111111111111111111111111111111111111111"
BLOCKED_ADDR = "0x9999999999999999999999999999999999999999"


class DemoWallet(WalletProvider):
    """AgentKit wallet provider with an execution counter — the honest proof point."""

    def __init__(self) -> None:
        self.executions = 0

    def get_address(self) -> str:
        return ALLOWED_ADDR

    def get_network(self) -> Network:
        return Network(protocol_family="evm", network_id="testnet", chain_id="84532")

    def get_balance(self) -> Decimal:
        return Decimal("1000")

    def sign_message(self, message: str) -> str:
        return "0x" + "ab" * 32

    def get_name(self) -> str:
        return "demo-wallet"

    def native_transfer(self, to: str, value: Decimal) -> str:
        self.executions += 1
        return "0xTX" + str(self.executions).zfill(8)


class GuardedAgentKit:
    """Integration shape: SpendShield sits BETWEEN the agent's decision and AgentKit execution.

    AgentKit's own loop has no policy hook, so the gate wraps the action boundary —
    the same place a LangChain/OpenAI-agents chatbot calls its tools.
    """

    def __init__(self, shield: SpendShield, wallet: DemoWallet) -> None:
        self.shield = shield
        self.wallet = wallet
        self.issuer = AuthorizationIssuer(secret=SECRET)
        self.executor = Executor(secret=SECRET)
        provider = WalletActionProvider()
        self.actions = {a.name: a for a in provider.get_actions(wallet)}

    # ── the gate ──────────────────────────────────────────────────────────
    def transfer(self, amount: float, to: str, human_approves=None) -> dict:
        """Authorize first. AgentKit executes only on ALLOW (or APPROVAL granted)."""
        res = self.shield.authorize(agent=AGENT, amount=amount, to=to)
        step = "SpendShield -> " + res.decision
        if res.decision == "DENY":
            return {"step": step, "reason": res.reason, "executed": False,
                    "wallet_executions": self.wallet.executions}

        if res.decision == "APPROVAL":
            if human_approves is None:
                return {"step": step + " (human approval required)", "executed": False,
                        "wallet_executions": self.wallet.executions}
            ok = human_approves(amount, to)
            if not ok:
                return {"step": step + " -> human DENIED", "executed": False,
                        "wallet_executions": self.wallet.executions}
            res = self.shield.approve(res.approval_id, by="human-demo")
            step += " -> human approved -> SpendShield -> " + res.decision
            if res.decision != "ALLOW":
                return {"step": step, "reason": res.reason, "executed": False,
                        "wallet_executions": self.wallet.executions}

        # ALLOW: sign a one-time grant and require the executor to consume it.
        grant = self.issuer.issue(agent=AGENT, amount=amount, currency="ETH",
                                  merchant=to, policy_version=res.policy_version)
        check_ok, check_reason = self.executor.verify(
            grant, agent=AGENT, amount=amount, currency="ETH", merchant=to,
            policy_version=res.policy_version)
        if not check_ok:
            return {"step": step + " -> grant REFUSED (" + check_reason + ")",
                    "executed": False, "wallet_executions": self.wallet.executions}

        # Only now does the AgentKit payment action run.
        action = self.actions["WalletActionProvider_native_transfer"]
        action.invoke({"to": to, "value": str(amount)})
        return {"step": step + " -> AgentKit EXECUTED", "grant": grant,
                "policy_version": res.policy_version,
                "executed": True, "wallet_executions": self.wallet.executions}

    def replay(self, grant: str, amount: float, to: str, policy_version: str = "") -> dict:
        """The same grant again must be refused — no second execution."""
        check_ok, check_reason = self.executor.verify(
            grant, agent=AGENT, amount=amount, currency="ETH", merchant=to,
            policy_version=policy_version)
        return {"step": "replay same grant -> " + ("ALLOW" if check_ok else "REFUSED (" + check_reason + ")"),
                "executed": check_ok, "wallet_executions": self.wallet.executions}


def main() -> int:
    shield = SpendShield(dry_run=False, log=lambda rec: None)  # keep demo output clean
    shield.load_policy(os.path.join(HERE, "policy.yaml"))
    kit = GuardedAgentKit(shield, DemoWallet())

    print("=" * 72)
    print("SpendShield × AgentKit — transfer authorization demo")
    print("=" * 72)

    # Scenario 1: small transfer to a trusted merchant -> ALLOW, executed once.
    s1 = kit.transfer(5, ALLOWED_ADDR)
    print(f"\nScenario 1: $5 transfer to trusted merchant\n  {s1['step']}\n  wallet executions: {s1['wallet_executions']}")

    # Scenario 2: above autonomous limit -> APPROVAL; human grants -> executed once; replay refused.
    s2 = kit.transfer(50, ALLOWED_ADDR, human_approves=lambda a, to: True)
    print(f"\nScenario 2: $50 transfer (over $30 autonomous limit)\n  {s2['step']}\n  wallet executions: {s2['wallet_executions']}")
    rp = kit.replay(s2.get("grant", ""), 50, ALLOWED_ADDR, s2.get("policy_version", ""))
    print(f"  {rp['step']}\n  wallet executions: {rp['wallet_executions']}")

    # Scenario 3: blocked recipient -> DENY, never invoked.
    s3 = kit.transfer(500, BLOCKED_ADDR)
    print(f"\nScenario 3: $500 transfer to blocked address\n  {s3['step']} ({s3.get('reason', '')})\n  wallet executions: {s3['wallet_executions']}")

    # ── assertions (the proof, not just the story) ────────────────────────
    checks = [
        ("ALLOW  -> AgentKit executed exactly 1 time", s1["executed"] and s1["wallet_executions"] == 1),
        ("APPROVAL -> human grant -> executed exactly once", s2["executed"] and s2["wallet_executions"] == 2),
        ("REUSED grant -> 0 additional executions", (not rp["executed"]) and rp["wallet_executions"] == 2),
        ("DENY (blocked merchant) -> AgentKit NOT invoked", (not s3["executed"]) and s3["wallet_executions"] == 2),
    ]
    print("\nAssertions (execution boundary):")
    all_ok = True
    for name, cond in checks:
        all_ok = all_ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

    print("\n" + ("ALL ASSERTIONS PASSED ✅" if all_ok else "SOME ASSERTIONS FAILED ❌"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
