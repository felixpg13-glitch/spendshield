# -*- coding: utf-8 -*-
"""SpendGuard 测试: 四道闸门全验证"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spendguard import SpendGuard, DryRunBlocked, BudgetExceeded, NeedsApproval, UnknownAgent


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
    guard = SpendGuard(dry_run=False, budget=100, approve_new_recipient=False)
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
    guard = SpendGuard(dry_run=False, approve_new_recipient=False)
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
    guard = SpendGuard(dry_run=False, rate_limit={"window_s": 60, "max_calls": 2},
                       approve_new_recipient=False)
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


# ============ 🔑 身份层(KYA 最小实现) ============

def test_agent_identity():
    """Agent 身份: 注册后放行, 审计带 agent 字段, 按 agent 记已花"""
    guard = SpendGuard(dry_run=False, budget=1000, approve_new_recipient=False)
    guard.register_agent("mcd_bot", budget=100)
    @guard.protect("下单", agent="mcd_bot")
    def place_order(amount, to):
        return "OK"
    place_order(amount=20, to="麦当劳")
    assert guard._agent_spent.get("mcd_bot") == 20.0
    assert guard.records[-1].agent == "mcd_bot"
    assert guard.records[-1].decision == "executed"
    print("✅ agent: 身份识别 + 审计留痕 + 按 agent 记已花")


def test_unknown_agent_rejected():
    """未注册 agent 默认拒绝(安全默认)"""
    guard = SpendGuard(dry_run=False)
    @guard.protect("下单")
    def place_order(amount, to):
        return "OK"
    try:
        place_order(amount=10, to="X", agent="hacker")
        assert False, "未注册 agent 应被拒绝"
    except UnknownAgent:
        pass
    assert guard.spent == 0
    assert guard.records[-1].agent == "hacker"
    assert guard.records[-1].decision == "blocked_unknown_agent"
    print("✅ unknown agent: 默认拒绝 + 留痕")


def test_allow_unknown_fallback():
    """allow_unknown=True 回落全局策略"""
    guard = SpendGuard(dry_run=False, budget=100, allow_unknown=True,
                       approve_new_recipient=False)
    @guard.protect("下单")
    def place_order(amount, to):
        return "OK"
    place_order(amount=10, to="X", agent="stranger")
    assert guard.spent == 10.0
    print("✅ allow_unknown: 回落全局策略")


def test_agent_budget_isolation():
    """agent 级预算隔离: A 花完不影响 B"""
    guard = SpendGuard(dry_run=False, budget=1000, approve_new_recipient=False)
    guard.register_agent("A", budget=50)
    guard.register_agent("B", budget=50)
    @guard.protect("下单")
    def place_order(amount, to, agent=""):
        return "OK"
    place_order(amount=50, to="a", agent="A")
    try:
        place_order(amount=10, to="a2", agent="A")
        assert False, "A 超预算应拦截"
    except BudgetExceeded:
        pass
    place_order(amount=50, to="b", agent="B")  # B 不受影响
    assert guard._agent_spent["B"] == 50.0
    assert any(r.agent == "A" and r.decision == "blocked_budget" for r in guard.records)
    print("✅ agent budget: 预算隔离")


def test_agent_blacklist_and_rate():
    """agent 级黑名单只拦该 agent; 频率限制按 agent+收款方"""
    guard = SpendGuard(dry_run=False, budget=1000, approve_new_recipient=False)
    guard.register_agent("A", blacklist=["骗子"], rate_limit={"window_s": 60, "max_calls": 2})
    @guard.protect("转账")
    def transfer(amount, to, agent=""):
        return "OK"
    try:
        transfer(amount=1, to="骗子收款", agent="A")
        assert False, "A 的黑名单应拒绝"
    except BudgetExceeded:
        pass
    transfer(amount=1, to="骗子收款")  # 无 agent(全局)不受 A 黑名单影响
    assert guard.spent == 1.0
    transfer(amount=1, to="正常商家", agent="A")
    transfer(amount=2, to="正常商家", agent="A")
    try:
        transfer(amount=3, to="正常商家", agent="A")
        assert False, "A 第3次应被频率拦截"
    except BudgetExceeded:
        pass
    transfer(amount=3, to="正常商家")  # 全局不受 A 频率影响
    assert guard.spent == 7.0
    print("✅ agent 黑名单/频率: 按 agent 隔离")


def test_policy_agents_yaml():
    """YAML agents 段加载身份策略"""
    import os, tempfile, yaml
    policy = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "spendguard.yaml.example")
    guard = SpendGuard(policy=policy)
    assert guard.allow_unknown is False
    assert "mcd_bot" in guard._agents
    assert guard._agents["mcd_bot"]["budget"] == 50
    assert "麦当劳" in guard._agents["mcd_bot"]["whitelist"]
    assert guard._agents["airguard_repair"]["approval"] == "tg"
    print("✅ policy agents: YAML 身份策略加载成功")


# ============ 🎯 意图一致性(提示注入解法) ============

def test_intent_new_recipient_denied_no_channel():
    """新收款方 + 未配置审批通道 → 直接拒绝(安全默认, 防提示注入)"""
    guard = SpendGuard(dry_run=False, approve_new_recipient=True)
    @guard.protect("转账")
    def transfer(amount, to):
        return "OK"
    try:
        transfer(amount=10, to="陌生收款方")
        assert False, "新收款方无审批通道应默认拒绝"
    except NeedsApproval:
        pass
    assert guard.spent == 0
    assert guard.records[-1].decision == "blocked_approval"
    assert "未配置审批通道" in guard.records[-1].reason
    print("✅ intent: 新收款方无审批通道 → 默认拒绝")


def test_intent_known_recipient_allowed():
    """交易成功的收款方进入记忆, 之后不再强制审批; 新收款方仍拒绝"""
    guard = SpendGuard(dry_run=False, approval=lambda rec: True, approve_new_recipient=True)
    @guard.protect("转账")
    def transfer(amount, to):
        return "OK"
    transfer(amount=10, to="老客户")   # 第一次: 审批通过 + 登记 known
    guard.approval = None              # 关掉全局审批
    transfer(amount=20, to="老客户")   # 已知收款方 → 不敏感 → 放行
    try:
        transfer(amount=5, to="新客户")  # 新收款方 → 无通道 → 拒绝
        assert False
    except NeedsApproval:
        pass
    assert guard.spent == 30.0
    print("✅ intent: 已知收款方免审批(记忆生效), 新收款方仍拒")


def test_intent_approve_above():
    """大额触发强制审批, 小额不触发"""
    guard = SpendGuard(dry_run=False, approve_above=100, approve_new_recipient=False)
    @guard.protect("转账")
    def transfer(amount, to):
        return "OK"
    transfer(amount=50, to="普通商家")   # 低于阈值 → 放行
    assert guard.spent == 50.0
    try:
        transfer(amount=150, to="普通商家")  # 超过阈值 → 无通道 → 拒绝
        assert False
    except NeedsApproval:
        pass
    assert guard.spent == 50.0
    print("✅ intent: 大额阈值触发强制审批")


def test_intent_with_approval_channel():
    """配置审批通道时, 新收款方走审批流程(可放行); 白名单跳过"""
    guard = SpendGuard(dry_run=False, approval=lambda rec: True,
                       approve_new_recipient=True, whitelist=["麦当劳"])
    @guard.protect("转账")
    def transfer(amount, to):
        return "OK"
    transfer(amount=10, to="新商家")     # 新收款方 → 走审批(通过) → 放行
    assert guard.spent == 10.0
    transfer(amount=10, to="麦当劳官方")  # 白名单 → 跳过审批
    assert guard.spent == 20.0
    print("✅ intent: 有审批通道时新收款方走审批, 白名单跳过")


# ============ 🔐 密钥保险库(Fireblocks 最小版) ============

def test_vault_store_retrieve():
    """加密存储 + 解密取回, 落盘无明文"""
    import tempfile, os as _os
    from spendguard import KeyVault
    mk = KeyVault.generate_key()
    with tempfile.TemporaryDirectory() as td:
        path = _os.path.join(td, "vault.json")
        vault = KeyVault(path, master_key=mk)
        vault.store("mcd_sk", "sk_live_123456")
        assert vault.retrieve("mcd_sk") == "sk_live_123456"
        raw = open(path, encoding="utf-8").read()
        assert "sk_live_123456" not in raw, "落盘文件不能含明文"
        print("✅ vault: 加密落盘, 文件无明文")


def test_vault_requires_master_key():
    """没有主密钥拒绝启动(主密钥不落盘原则)"""
    import os as _os
    from spendguard import KeyVault
    _os.environ.pop("SPENDGUARD_MASTER_KEY", None)
    try:
        KeyVault("/tmp/x_vault_test.json")
        assert False, "缺主密钥应报错"
    except ValueError:
        pass
    print("✅ vault: 缺主密钥拒绝启动")


def test_secret_access_via_guard():
    """guard.get_secret 过闸门: 注册 agent + 密钥名白名单 → 取到 + 审计"""
    import tempfile, os as _os
    from spendguard import KeyVault
    with tempfile.TemporaryDirectory() as td:
        vault = KeyVault(_os.path.join(td, "vault.json"), master_key=KeyVault.generate_key())
        vault.store("mcd_sk", "sk_live_secret")
        guard = SpendGuard(dry_run=False, key_vault=vault, approve_new_recipient=False)
        guard.register_agent("mcd_bot", whitelist=["mcd_sk"])
        sk = guard.get_secret("mcd_sk", agent="mcd_bot")
        assert sk == "sk_live_secret"
        assert guard.records[-1].decision == "secret_access"
        assert guard.records[-1].agent == "mcd_bot"
        print("✅ get_secret: 过闸门取密钥 + 审计留痕")


def test_secret_denied_unknown_agent():
    """未注册 agent 取密钥被拒"""
    import tempfile, os as _os
    from spendguard import KeyVault
    with tempfile.TemporaryDirectory() as td:
        vault = KeyVault(_os.path.join(td, "vault.json"), master_key=KeyVault.generate_key())
        vault.store("mcd_sk", "sk_live_secret")
        guard = SpendGuard(dry_run=False, key_vault=vault)
        try:
            guard.get_secret("mcd_sk", agent="hacker")
            assert False, "未注册 agent 取密钥应被拒"
        except UnknownAgent:
            pass
        print("✅ get_secret: 未注册 agent 拒绝")


def test_secret_denied_new_name_no_channel():
    """新密钥名 + 无审批通道 → 默认拒绝(防提示注入偷密钥)"""
    import tempfile, os as _os
    from spendguard import KeyVault
    with tempfile.TemporaryDirectory() as td:
        vault = KeyVault(_os.path.join(td, "vault.json"), master_key=KeyVault.generate_key())
        vault.store("admin_key", "super_secret")
        guard = SpendGuard(dry_run=False, key_vault=vault)
        guard.register_agent("mcd_bot")
        try:
            guard.get_secret("admin_key", agent="mcd_bot")
            assert False, "新密钥名无审批通道应拒绝"
        except NeedsApproval:
            pass
        print("✅ get_secret: 新密钥名默认拒绝(防偷密钥)")
