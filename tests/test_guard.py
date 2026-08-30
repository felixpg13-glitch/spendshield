# -*- coding: utf-8 -*-
"""SpendGuard 测试: 四道闸门全验证"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spendguard import SpendGuard, DryRunBlocked, BudgetExceeded, NeedsApproval


def test_dry_run():
    guard = SpendGuard(dry_run=True)
    executed = []
    @guard.protect("下单")
    def place_order(amount, to):
        executed.append(amount)
        return "OK"
    try:
        place_order(amount=99, to="麦当劳")
        assert False, "dry_run 应该拦截"
    except DryRunBlocked:
        pass
    assert executed == [], "干跑模式下绝不能真实执行"
    print("✅ dry_run: 拦截成功, 未执行")


def test_budget():
    guard = SpendGuard(dry_run=False, budget=100)
    @guard.protect("下单")
    def place_order(amount, to):
        return "OK"
    place_order(amount=30, to="A")
    place_order(amount=50, to="B")
    try:
        place_order(amount=30, to="C")  # 30+50+30 > 100
        assert False, "应该超预算"
    except BudgetExceeded:
        pass
    assert abs(guard.spent - 80.0) < 1e-6
    print(f"✅ budget: 拦截成功, 已花 {guard.spent}")


def test_max_amount():
    guard = SpendGuard(dry_run=False, budget=1000)
    @guard.protect("大额转账", max_amount=500)
    def transfer(amount, to):
        return "OK"
    try:
        transfer(amount=9999, to="骗子")
        assert False
    except BudgetExceeded:
        pass
    print("✅ max_amount: 单次超限拦截")


def test_approval_deny():
    guard = SpendGuard(dry_run=False, approval=lambda rec: False)
    @guard.protect("下单")
    def place_order(amount, to):
        return "OK"
    try:
        place_order(amount=10, to="X")
        assert False
    except NeedsApproval:
        pass
    assert guard.spent == 0
    print("✅ approval: 拒绝后未扣款")


def test_approval_allow_and_audit():
    guard = SpendGuard(dry_run=False, approval=lambda rec: True)
    @guard.protect("下单")
    def place_order(amount, to):
        return "OK"
    place_order(amount=20, to="Y")
    assert abs(guard.spent - 20.0) < 1e-6
    recs = guard.records
    assert recs[-1].decision == "executed"
    assert len(recs) >= 1
    print(f"✅ approval+audit: 放行并留痕 ({len(recs)} 条记录)")


def test_failed_execution_no_charge():
    guard = SpendGuard(dry_run=False)
    @guard.protect("下单")
    def place_order(amount, to):
        raise RuntimeError("上游失败")
    try:
        place_order(amount=50, to="Z")
    except RuntimeError:
        pass
    assert guard.spent == 0, "执行失败不能算已花"
    assert guard.records[-1].decision == "failed"
    print("✅ failed: 执行失败不计费, 留痕 failed")


if __name__ == "__main__":
    test_dry_run()
    test_budget()
    test_max_amount()
    test_approval_deny()
    test_approval_allow_and_audit()
    test_failed_execution_no_charge()
    print("\n🎉 全部测试通过")
