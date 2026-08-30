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


def test_blacklist():
    """黑名单收款方直接拒绝"""
    guard = SpendGuard(dry_run=False, blacklist=["未知供应商", "骗子"])
    @guard.protect("转账")
    def transfer(amount, to):
        return "OK"
    try:
        transfer(amount=100, to="骗子收款方")
        assert False, "黑名单应拒绝"
    except BudgetExceeded:
        pass
    assert guard.spent == 0
    assert guard.records[-1].decision == "blocked_blacklist"
    print("✅ blacklist: 黑名单拒绝 + 留痕")


def test_whitelist_skips_approval():
    """白名单收款方跳过人工确认"""
    guard = SpendGuard(dry_run=False, approval=lambda rec: False, whitelist=["麦当劳"])
    @guard.protect("下单")
    def place_order(amount, to):
        return "OK"
    place_order(amount=20, to="麦当劳官方")
    assert guard.spent == 20.0
    print("✅ whitelist: 白名单跳过确认放行")


def test_rate_limit():
    """同收款方频率限制"""
    guard = SpendGuard(dry_run=False, rate_limit={"window_s": 60, "max_calls": 2})
    @guard.protect("下单")
    def place_order(amount, to):
        return "OK"
    place_order(amount=1, to="A")
    place_order(amount=2, to="A")
    try:
        place_order(amount=3, to="A")
        assert False, "第3次应被频率限制拦截"
    except BudgetExceeded:
        pass
    assert guard.spent == 3.0  # 只执行了前两次
    assert guard.records[-1].decision == "blocked_rate"
    print("✅ rate_limit: 频率限制拦截 + 留痕")


def test_policy_file():
    """策略文件加载(策略即代码)"""
    import os, tempfile
    policy = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "spendguard.yaml.example")
    guard = SpendGuard(policy=policy)
    assert guard.dry_run is True
    assert guard.budget == 200
    assert "未知供应商" in guard.blacklist
    assert "麦当劳" in guard.whitelist
    assert guard.rate_limit.get("max_calls") == 3
    print("✅ policy: YAML 策略加载成功")


def test_webhook_approval():
    """Webhook 远程审批(起本地 mock 审核服务)"""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received = {}
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            import json as j
            received.update(j.loads(body))
            resp = j.dumps({"approved": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        def log_message(self, *a): pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    guard = SpendGuard(dry_run=False, approval="webhook",
                       webhook_url=f"http://127.0.0.1:{port}/approve")
    @guard.protect("下单")
    def place_order(amount, to):
        return "OK"
    place_order(amount=50, to="商家A")
    assert guard.spent == 50.0
    assert received.get("action") == "下单"
    assert received.get("to") == "商家A"
    srv.shutdown()
    print("✅ webhook: 远程审批放行 + 审核服务收到请求")


def test_webhook_approval_deny():
    """Webhook 拒绝时不放行"""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json as j

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            resp = j.dumps({"approved": False}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        def log_message(self, *a): pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    guard = SpendGuard(dry_run=False, approval="webhook",
                       webhook_url=f"http://127.0.0.1:{port}/approve")
    @guard.protect("转账")
    def transfer(amount, to):
        return "OK"
    try:
        transfer(amount=999, to="陌生人")
        assert False, "webhook 拒绝应拦截"
    except NeedsApproval:
        pass
    assert guard.spent == 0
    srv.shutdown()
    print("✅ webhook: 拒绝时拦截 + 不扣款")
