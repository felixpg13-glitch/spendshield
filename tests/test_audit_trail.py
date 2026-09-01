# -*- coding: utf-8 -*-
"""Audit Trail 证据层: 哈希链完整性 / 5问可答 / query / export / rotation"""
import sys, os, tempfile, yaml, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from spendshield import SpendShield

POLICY = {
    "version": "2.0.0",
    "policy": {"budget": {"daily": 100}, "transaction": {"max": 50},
               "merchants": {"allowed": ["amazon.com"], "blocked": ["scam.com"]},
               "approval": {"over": 30, "new_merchant": False, "channel": "tg"}},
    "agents": {"bot": {"approval": {"over": 30, "new_merchant": False, "channel": "tg"}}},
}


def _guard():
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(POLICY, f); f.close()
    g = SpendShield(dry_run=False)
    g.load_policy(f.name)
    os.unlink(f.name)
    return g


# ── 哈希链完整性 ─────────────────────────────────────────
def test_chain_integrity_and_tamper_detection():
    g = _guard()
    g.authorize("bot", 20, "amazon.com")    # ALLOW
    r = g.authorize("bot", 40, "amazon.com")  # APPROVAL
    g.approve(r.approval_id, by="felix")
    ok, msg = g.audit.verify_chain()
    assert ok, msg
    # 篡改检测: 改一个事件的金额 → 链断裂
    g.audit.events[0].amount = 9999
    ok, msg = g.audit.verify_chain()
    assert not ok
    assert "篡改" in msg


def test_chain_integrity_after_tamper_then_restore():
    g = _guard()
    g.authorize("bot", 20, "amazon.com")
    orig = g.audit.events[0].event_hash
    g.audit.events[0].amount = 9999
    assert not g.audit.verify_chain()[0]
    g.audit.events[0].amount = 20.0   # 保持 float 类型(JSON 序列化一致)
    g.audit.events[0].event_hash = orig
    assert g.audit.verify_chain()[0]


# ── 5 问可答 ─────────────────────────────────────────────
def test_transaction_chain_answers_5_questions():
    g = _guard()
    r = g.authorize("bot", 40, "amazon.com", meta={"request_id": "req-5q"})
    assert r.decision == "APPROVAL"
    g.approve(r.approval_id, by="felix")
    chain = g.audit.transaction_chain("req-5q")
    assert len(chain) == 2
    auth_ev, appr_ev = chain[0], chain[1]
    # 1. 谁发起的
    assert auth_ev["actor"] == "bot"
    # 2. 哪个 Policy
    assert auth_ev["policy_version"] == "2.0.0"
    assert auth_ev["policy_hash"]
    # 3. 哪个 Engine
    assert auth_ev["engine_version"]
    # 4. 为什么 APPROVAL
    assert auth_ev["decision"] == "APPROVAL"
    assert "APPROVAL_REQUIRED" in auth_ev["reason_codes"]
    assert "$40.00" in auth_ev["primary_reason"]
    # 5. 状态怎么变到这里: requested → approved
    assert auth_ev["approval_state"] == "requested"
    assert appr_ev["approval_state"] == "approved"
    assert appr_ev["request_id"] == "req-5q"
    assert appr_ev["decision"] == "ALLOW"


def test_reject_chain():
    g = _guard()
    r = g.authorize("bot", 40, "amazon.com", meta={"request_id": "req-rj"})
    g.reject(r.approval_id, by="boss")
    chain = g.audit.transaction_chain("req-rj")
    assert chain[1]["approval_state"] == "rejected"
    assert chain[1]["decision"] == "DENY"


# ── 记录当时策略, 不是事后重算 ───────────────────────────
def test_policy_hash_is_snapshot_not_recomputed():
    """策略变更后, 历史事件的 policy_hash 仍指向当时的策略"""
    g = _guard()
    g.authorize("bot", 20, "amazon.com", meta={"request_id": "req-ph"})
    old_hash = g.audit.transaction_chain("req-ph")[0]["policy_hash"]
    # 换策略
    g._setup_v2({"version": "3.0", "policy": {"transaction": {"max": 999},
                                              "approval": {"over": 0, "new_merchant": False, "channel": ""}}})
    assert g._policy_fp() != old_hash   # 现在策略指纹变了
    ev = g.audit.transaction_chain("req-ph")[0]
    assert ev["policy_hash"] == old_hash   # 历史事件仍记录当时的策略


# ── Query ────────────────────────────────────────────────
def test_query_filters():
    g = _guard()
    for i in range(5):
        g.authorize("bot", 20, "amazon.com", meta={"request_id": f"q-{i}"})
    g.authorize("bot", 75, "amazon.com", meta={"request_id": "q-deny"})
    # decision 过滤
    denies = g.audit.query(decision="DENY")
    assert len(denies) == 1 and denies[0]["request_id"] == "q-deny"
    # reason_code 过滤
    by_code = g.audit.query(reason_code="MAX_TRANSACTION_EXCEEDED")
    assert len(by_code) == 1
    # actor 过滤
    actors = g.audit.query(actor="bot")
    assert len(actors) == 6
    # request_id 过滤
    one = g.audit.query(request_id="q-2")
    assert len(one) == 1


def test_query_get_event():
    g = _guard()
    g.authorize("bot", 20, "amazon.com")
    ev = g.audit.events[0]
    assert g.audit.get(ev.event_id)["event_id"] == ev.event_id
    assert g.audit.get("nonexistent") is None


# ── Export ───────────────────────────────────────────────
def test_export_jsonl_csv(tmp_path):
    g = _guard()
    g.authorize("bot", 20, "amazon.com")
    g.authorize("bot", 75, "amazon.com")
    j = g.audit.export_json()
    assert json.loads(j)[0]["event_hash"]
    jl = g.audit.export_jsonl()
    lines = jl.strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["event_hash"]
    csv_out = g.audit.export_csv()
    assert "request_id" in csv_out and "event_hash" in csv_out
    # 写文件
    p1 = g.audit.export_jsonl(str(tmp_path / "audit.jsonl"))
    assert os.path.exists(p1)


# ── Rotation(克制: 归档不破坏链) ─────────────────────────
def test_rotate_preserves_chain():
    g = _guard()
    for i in range(15):
        g.authorize("bot", 20, "amazon.com", meta={"request_id": f"r-{i}"})
    assert g.audit.verify_chain()[0]
    res = g.audit.rotate(keep=5, archive_path="/tmp/ss_audit_archive.jsonl")
    assert res["archived"] == 10 and res["kept"] == 5
    # 旋转后链仍完整(新链从归档尾部续接)
    assert g.audit.verify_chain()[0]
    # 归档文件存在且含旧事件
    assert os.path.exists("/tmp/ss_audit_archive.jsonl")
    with open("/tmp/ss_audit_archive.jsonl", encoding="utf-8") as f:
        archived = f.read().strip().split("\n")
    assert len(archived) == 10
    # 新链第一个事件的 previous = 归档最后一个的 event_hash(无缝续接)
    assert g.audit.events[0].previous_event_hash == json.loads(archived[-1])["event_hash"]
    os.unlink("/tmp/ss_audit_archive.jsonl")
    # 旋转后还能继续写+验证
    g.authorize("bot", 20, "amazon.com")
    assert g.audit.verify_chain()[0]


# ── policy_apply 审计 ────────────────────────────────────
def test_policy_apply_audited():
    from spendshield.mcp_server import SpendShieldMCP
    g = _guard()
    m = SpendShieldMCP(g)
    m._policy_apply({"policy": json.dumps({"version": "9.0", "policy": {}})})
    evs = [e for e in g.audit.events if e.action == "policy_apply"]
    assert len(evs) == 1
    assert evs[0].policy_version == "9.0"   # 事件记录新版本
    assert g.audit.verify_chain()[0]
