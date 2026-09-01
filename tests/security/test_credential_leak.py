# -*- coding: utf-8 -*-
"""
Security Suite ⑧ — credential_leak
攻击: meta/请求里带密钥, 试图让密钥出现在结果/审计/异常中。
断言: 敏感键在输出层全部脱敏。
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from spendshield import SpendShield

POLICY = {
    "version": "2.0.0",
    "policy": {
        "budget": {"daily": 1000},
        "transaction": {"max": 100},
        "merchants": {"allowed": ["amazon.com"]},
        "approval": {"over": 0, "new_merchant": False, "channel": ""},
    },
    "agents": {"bot": {"approval": {"over": 30, "new_merchant": False, "channel": "tg"}}},
}

SECRETS = {
    "api_key": "sk-live-ABC123",
    "token": "ghp_SECRETTOKEN",
    "password": "hunter2",
    "authorization": "Bearer super-secret",
    "card_number": "4111-1111-1111-1111",
}


def _make_guard():
    import tempfile, yaml, os
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(POLICY, f)
        path = f.name
    g = SpendShield(dry_run=False)
    g.load_policy(path)
    os.unlink(path)
    return g


def test_result_dict_redacts_sensitive_meta():
    g = _make_guard()
    r = g.authorize("bot", 10, "amazon.com", meta=dict(SECRETS))
    d = json.dumps(r.to_dict(), ensure_ascii=False)
    for v in SECRETS.values():
        assert v not in d, f"泄漏: {v}"
    assert "***REDACTED***" in d


def test_deny_result_redacts_too():
    g = _make_guard()
    r = g.authorize("bot", 500, "amazon.com", meta={"secret": "hush", "idempotency_key": "k"})
    assert r.decision == "DENY"
    d = json.dumps(r.to_dict(), ensure_ascii=False)
    assert "hush" not in d


def test_approval_result_redacts():
    g = _make_guard()
    r = g.authorize("bot", 40, "amazon.com", meta={"password": "p@ss"})
    assert r.decision == "APPROVAL"
    a = g.approve(r.approval_id, by="felix")
    d = json.dumps(a.to_dict(), ensure_ascii=False)
    assert "p@ss" not in d
    assert "***REDACTED***" in d


def test_audit_records_no_meta_at_all():
    """审计记录(AuditRecord)根本不落 meta → 无泄漏面"""
    g = _make_guard()
    g.authorize("bot", 10, "amazon.com", meta={"secret": "s3cr3t"})
    rec = g.records[-1]
    d = rec.to_dict()
    assert "s3cr3t" not in json.dumps(d)
    assert "meta" not in d


def test_export_audit_safe():
    g = _make_guard()
    g.authorize("bot", 10, "amazon.com", meta={"secret": "s3cr3t"})
    path = g.export_audit("/tmp/ss_audit_test.json")
    content = open(path, encoding="utf-8").read()
    assert "s3cr3t" not in content
    os.unlink(path)


def test_exceptions_do_not_leak():
    """异常路径: 未知 agent 的 reason 不含 meta"""
    g = _make_guard()
    r = g.authorize("hacker", 10, "amazon.com", meta={"token": "leak-me"})
    assert r.decision == "DENY"
    assert "leak-me" not in r.reason
    assert "leak-me" not in json.dumps(r.to_dict())
