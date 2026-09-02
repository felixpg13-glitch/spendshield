# -*- coding: utf-8 -*-
"""Enforcement 原型测试: 无授权不动钱 · 篡改/伪造/过期/重放全 FAIL"""
import time
import pytest

from spendshield.enforce import AuthorizationIssuer, Executor

SECRET = "test-secret-0123456789"


@pytest.fixture()
def pair():
    issuer = AuthorizationIssuer(secret=SECRET)
    executor = Executor(secret=SECRET)
    return issuer, executor


def _intent(**over):
    base = dict(agent="claude", amount=25.0, currency="USD", merchant="mcdonalds.com",
                policy_version="2.1.0")
    base.update(over)
    return base


def test_happy_path_allows_exact_intent(pair):
    issuer, executor = pair
    tok = issuer.issue(**_intent())
    ok, reason = executor.verify(tok, **_intent())
    assert ok and reason == "AUTHORIZED"


def test_no_token_refuses(pair):
    _, executor = pair
    ok, reason = executor.verify("", **_intent())
    assert not ok and reason == "MALFORMED_TOKEN"


def test_tampered_amount_fails(pair):
    issuer, executor = pair
    tok = issuer.issue(**_intent())
    ok, reason = executor.verify(tok, **{**_intent(), "amount": 250.0})  # 执行方被改成 $250
    assert not ok and reason == "FIELD_MISMATCH:amount"


def test_tampered_merchant_fails(pair):
    issuer, executor = pair
    tok = issuer.issue(**_intent())
    ok, reason = executor.verify(tok, **{**_intent(), "merchant": "amazon.com"})
    assert not ok and reason == "FIELD_MISMATCH:merchant"


def test_tampered_currency_fails(pair):
    issuer, executor = pair
    tok = issuer.issue(**_intent())
    ok, reason = executor.verify(tok, **{**_intent(), "currency": "CNY"})
    assert not ok and reason == "FIELD_MISMATCH:currency"


def test_tampered_policy_version_fails(pair):
    issuer, executor = pair
    tok = issuer.issue(**_intent())
    ok, reason = executor.verify(tok, **{**_intent(), "policy_version": "1.0.0"})
    assert not ok and reason == "FIELD_MISMATCH:policy_version"


def test_expired_fails(pair):
    issuer, executor = pair
    tok = issuer.issue(**_intent(), ttl=-10)
    ok, reason = executor.verify(tok, **_intent())
    assert not ok and reason == "EXPIRED"


def test_reuse_fails(pair):
    issuer, executor = pair
    tok = issuer.issue(**_intent())
    assert executor.verify(tok, **_intent())[0]
    ok, reason = executor.verify(tok, **_intent())  # 重放
    assert not ok and reason == "REUSED"


def test_forged_signature_fails(pair):
    issuer, executor = pair
    tok = issuer.issue(**_intent())
    forged = tok[:-4] + ("f" if tok[-4] != "f" else "e")  # 改签名尾部
    ok, reason = executor.verify(forged, **_intent())
    assert not ok and reason == "INVALID_SIGNATURE"


def test_wrong_secret_executor_refuses_valid_token(pair):
    issuer, _ = pair
    tok = issuer.issue(**_intent())
    evil = Executor(secret="attacker-secret")
    ok, reason = evil.verify(tok, **_intent())
    assert not ok and reason == "INVALID_SIGNATURE"


def test_concurrent_double_spend_exactly_one_wins(pair):
    """并发双花: 20 个线程同时 verify 同一 token → 恰好 1 个 AUTHORIZED, 其余 REUSED"""
    from concurrent.futures import ThreadPoolExecutor

    issuer, executor = pair
    tok = issuer.issue(**_intent())

    def hit(_):
        return executor.verify(tok, **_intent())

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(hit, range(50)))

    allowed = [r for r in results if r == (True, "AUTHORIZED")]
    reused = [r for r in results if r == (False, "REUSED")]
    assert len(allowed) == 1, f"并发双花! 放行了 {len(allowed)} 次"
    assert len(reused) == 49


# ---- provenance / authorship-channel 攻击类(yairsabag/Verb Authority 提出, 2026-09-02) ----
# 值没变, 来源变了 → 必须 fail closed。grant 绑定“声明的来源标签”, 签名覆盖。

def _src_intent(src_agent="trusted_app", src_amount="trusted_args"):
    return dict(agent="claude", amount=25.0, currency="USD", merchant="mcdonalds.com",
                policy_version="2.1.0", sources={"amount": src_amount, "merchant": src_agent})


def test_source_ok_when_declared_and_matching(pair):
    issuer, executor = pair
    tok = issuer.issue(**_src_intent())
    ok, reason = executor.verify(tok, **{**_src_intent(), "sources": {"amount": "trusted_args", "merchant": "trusted_app"}})
    assert ok and reason == "AUTHORIZED"


def test_protected_value_source_substitution_denied(pair):
    """A: 值一样($25), 但来源从 trusted_args 换到 agent_controlled → 拒"""
    issuer, executor = pair
    tok = issuer.issue(**_src_intent())                      # 签发: amount_source=trusted_args
    ok, reason = executor.verify(tok, **_src_intent(src_amount="agent_controlled"))
    assert not ok and reason == "SOURCE_MISMATCH:amount"


def test_missing_source_binding_denied(pair):
    """B1: grant 绑定了来源, 执行时没声明 → 拒(fail closed)"""
    issuer, executor = pair
    tok = issuer.issue(**_src_intent())
    ok, reason = executor.verify(tok, agent="claude", amount=25.0, currency="USD",
                                 merchant="mcdonalds.com", policy_version="2.1.0")  # 无 sources
    assert not ok and reason == "SOURCE_MISSING:amount"


def test_altered_source_binding_denied(pair):
    """B2: 来源标签被改成另一个 → 拒"""
    issuer, executor = pair
    tok = issuer.issue(**_src_intent())
    ok, reason = executor.verify(tok, **{**_src_intent(), "sources": {"amount": "peer_output", "merchant": "trusted_app"}})
    assert not ok and reason == "SOURCE_MISMATCH:amount"


def test_tool_output_cannot_be_reused_as_authority(pair):
    """C1: 工具输出里的 'approved $25' 被 agent 拿去当可信来源 → 值同源异 → 拒"""
    issuer, executor = pair
    tok = issuer.issue(**_src_intent())
    # 恶意网页/工具输出诱导 agent 以 agent_controlled 身份提交同一 $25
    ok, reason = executor.verify(tok, **_src_intent(src_amount="agent_controlled", src_agent="tool_output"))
    assert not ok and reason in ("SOURCE_MISMATCH:amount", "SOURCE_MISMATCH:merchant")


def test_peer_output_cannot_be_reused_as_authority(pair):
    """C2: 另一个 agent 声称 'allowed to spend $25' 不能成为授权来源"""
    issuer, executor = pair
    tok = issuer.issue(**_src_intent())
    ok, reason = executor.verify(tok, **_src_intent(src_agent="peer_agent"))
    assert not ok and reason == "SOURCE_MISMATCH:merchant"


def test_legacy_grant_without_sources_still_works(pair):
    """没声明 sources 的老 grant 行为不变(向后兼容)"""
    issuer, executor = pair
    tok = issuer.issue(agent="claude", amount=25.0, currency="USD", merchant="mcdonalds.com", policy_version="2.1.0")
    ok, reason = executor.verify(tok, agent="claude", amount=25.0, currency="USD",
                                 merchant="mcdonalds.com", policy_version="2.1.0")
    assert ok and reason == "AUTHORIZED"
