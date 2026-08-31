# -*- coding: utf-8 -*-
"""x402 适配层测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spendshield import SpendShield, DryRunBlocked, BudgetExceeded, NeedsApproval, UnknownAgent
from spendshield.adapters.x402 import X402PaywallGuard, protect_x402_payment, confirm_x402_payment, resource_price_to_amount


def test_price_parsing():
    """x402 各种价格格式都能转 float"""
    assert resource_price_to_amount("0.01") == 0.01
    assert resource_price_to_amount(0.05) == 0.05
    assert resource_price_to_amount(1) == 1.0
    # AssetAmount 形态
    class FakeAmount:
        amount = "0.02"
    assert resource_price_to_amount(FakeAmount()) == 0.02
    assert resource_price_to_amount({"amount": 0.03}) == 0.03
    print("✅ price 解析: 全部格式 OK")


def test_paywall_guard_dry_run():
    """服务端: dry_run 模式下结算被拦(不真付)"""
    guard = SpendShield(dry_run=True)
    pw = X402PaywallGuard(guard)
    try:
        pw.authorize_resource("weather-api", price="0.01", pay_to="0xabc")
        assert False, "dry_run 应拦截"
    except DryRunBlocked:
        pass
    assert guard.records[-1].decision == "dry_run"
    print("✅ paywall: dry_run 拦截结算")


def test_paywall_guard_budget():
    """服务端: 预算超支拦截(authorize + confirm 累计)"""
    guard = SpendShield(dry_run=False, budget=0.02, approve_new_recipient=False)
    pw = X402PaywallGuard(guard)
    pw.authorize_resource("api-a", price="0.01", pay_to="seller-a")
    pw.confirm_payment("api-a", price="0.01", pay_to="seller-a")
    try:
        pw.authorize_resource("api-b", price="0.02", pay_to="seller-b")
        assert False, "超预算应拦截"
    except BudgetExceeded:
        pass
    print("✅ paywall: 预算拦截")


def test_paywall_guard_new_recipient():
    """服务端: 新收款方(无审批通道)默认拒绝 = 意图一致性"""
    guard = SpendShield(dry_run=False, approve_new_recipient=True)
    pw = X402PaywallGuard(guard)
    try:
        pw.authorize_resource("api-c", price="0.01", pay_to="stranger.xyz")
        assert False, "新收款方应拒绝"
    except NeedsApproval:
        pass
    print("✅ paywall: 新收款方默认拒绝")


def test_client_protect_payment():
    """客户端: 支付前过闸门, 通过后可继续(注册 agent)"""
    guard = SpendShield(dry_run=False, budget=1.0, approve_new_recipient=False)
    guard.register_agent("research_bot", budget=1.0)
    ok = protect_x402_payment(guard, amount=0.01, to="weather.example.com", agent="research_bot")
    assert ok is True
    confirm_x402_payment(guard, amount=0.01, to="weather.example.com", agent="research_bot")
    assert guard.records[-1].decision == "executed"
    assert guard._agent_spent["research_bot"] == 0.01
    print("✅ client: 支付前闸门放行 + 确认累计")


def test_client_unknown_agent():
    """客户端: 未注册 agent 默认拒绝"""
    guard = SpendShield(dry_run=False)
    try:
        protect_x402_payment(guard, amount=0.01, to="x.com", agent="hacker")
        assert False, "未注册 agent 应拒绝"
    except UnknownAgent:
        pass
    print("✅ client: 未注册 agent 拒绝")


if __name__ == "__main__":
    test_price_parsing()
    test_paywall_guard_dry_run()
    test_paywall_guard_budget()
    test_paywall_guard_new_recipient()
    test_client_protect_payment()
    test_client_unknown_agent()
    print("\n🎉 x402 适配层测试全过")
