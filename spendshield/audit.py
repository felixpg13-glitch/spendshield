# -*- coding: utf-8 -*-
"""
SpendShield Audit Trail — 可追责证据层

设计原则(Felix 定):
  1. 记录「当时系统为什么做这个决定」, 不是事后重算 → policy_hash/policy_version 快照
  2. 哈希链: event_hash + previous_event_hash → 篡改可检测(密码学完整性)
  3. 5 问可答: 谁发起 / 哪个 Policy / 哪个 Engine / 为什么 / 状态怎么变到这里的
  4. 导出 JSONL → SIEM / Data Lake 直接消费
  5. Rotation 不破坏完整性/顺序/关联

事件流: authorize → approve/reject → 每个事件带 request_id 关联成链。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


def _sha256(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()


@dataclass
class AuditEvent:
    event_id: str
    ts: float
    engine_version: str
    policy_version: str
    policy_hash: str              # 评估时点的策略指纹快照(防事后重算漂移)
    request_id: str               # 关联同一笔交易的所有事件
    actor: str                    # agent 身份
    action: str                   # authorize / approve / reject / policy_apply / reset
    decision: str                 # ALLOW / APPROVAL / DENY / ERROR
    reason_codes: list = field(default_factory=list)
    primary_reason: str = ""
    amount: float = 0.0
    currency: str = ""
    merchant: str = ""
    approval_state: str = ""      # none / requested / approved / rejected
    input_hash: str = ""
    meta: dict = field(default_factory=dict)     # 已脱敏
    previous_event_hash: str = "" # 哈希链
    event_hash: str = ""

    def compute_hash(self) -> str:
        d = asdict(self)
        d.pop("event_hash", None)
        return _sha256(d)

    def finalize(self, prev_hash: str) -> "AuditEvent":
        self.previous_event_hash = prev_hash
        self.event_hash = self.compute_hash()
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


class AuditLog:
    """事件存储 + 哈希链 + 查询 + 导出 + 旋转(克制版)"""

    def __init__(self, archive_dir: Optional[str] = None):
        self.events: list[AuditEvent] = []
        self.archive_dir = archive_dir
        self._chain_root: str = "GENESIS"   # 链起点 previous(旋转后 = 归档尾部 hash)
        self._by_request: dict[str, list[int]] = {}   # request_id -> event 索引

    # ── 写入(唯一入口, 保证链完整性) ──
    def append(self, **kw) -> AuditEvent:
        prev = self.events[-1].event_hash if self.events else self._chain_root
        ev = AuditEvent(event_id=uuid.uuid4().hex[:16], ts=time.time(), **kw)
        ev.finalize(prev)
        self.events.append(ev)
        self._by_request.setdefault(ev.request_id, []).append(len(self.events) - 1)
        return ev

    # ── 完整性 ──
    def verify_chain(self) -> tuple[bool, str]:
        """验证整条哈希链: 任何一环被改 → False"""
        prev = self._chain_root
        for i, ev in enumerate(self.events):
            if ev.previous_event_hash != prev:
                return False, f"event[{i}] {ev.event_id}: previous_event_hash 断裂"
            if ev.event_hash != ev.compute_hash():
                return False, f"event[{i}] {ev.event_id}: 内容被篡改(event_hash 不匹配)"
            prev = ev.event_hash
        return True, f"chain ok ({len(self.events)} events)"

    def verify_request(self, request_id: str) -> tuple[bool, str]:
        """验证单个 request 的事件链完整性"""
        idxs = self._by_request.get(request_id, [])
        if not idxs:
            return False, f"request_id {request_id} 无事件"
        prev = "GENESIS" if idxs[0] == 0 else self.events[idxs[0] - 1].event_hash
        for i in idxs:
            ev = self.events[i]
            if ev.previous_event_hash != prev:
                return False, f"{ev.event_id}: 链断裂"
            if ev.event_hash != ev.compute_hash():
                return False, f"{ev.event_id}: 篡改"
            prev = ev.event_hash
        return True, f"request {request_id} chain ok ({len(idxs)} events)"

    # ── 查询(5 问可答) ──
    def get(self, event_id: str) -> Optional[dict]:
        for ev in self.events:
            if ev.event_id == event_id:
                return ev.to_dict()
        return None

    def query(self, request_id: str = "", actor: str = "", decision: str = "",
              reason_code: str = "", action: str = "",
              time_start: float = 0.0, time_end: float = 0.0,
              limit: int = 100) -> list[dict]:
        out = []
        for ev in reversed(self.events):   # 最新在前
            if request_id and ev.request_id != request_id:
                continue
            if actor and ev.actor != actor:
                continue
            if decision and ev.decision != decision:
                continue
            if reason_code and reason_code not in ev.reason_codes:
                continue
            if action and ev.action != action:
                continue
            if time_start and ev.ts < time_start:
                continue
            if time_end and ev.ts > time_end:
                continue
            out.append(ev.to_dict())
            if len(out) >= limit:
                break
        return out

    def transaction_chain(self, request_id: str) -> list[dict]:
        """一笔交易的完整事件链(authorize → approve → final state)"""
        return [self.events[i].to_dict() for i in self._by_request.get(request_id, [])]

    # ── 导出 ──
    def export_json(self, path: str = "") -> str:
        data = [ev.to_dict() for ev in self.events]
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            return path
        return json.dumps(data, ensure_ascii=False, default=str)

    def export_jsonl(self, path: str = "") -> str:
        lines = [ev.to_jsonl() for ev in self.events]
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return path
        return "\n".join(lines)

    def export_csv(self, path: str = "") -> str:
        buf = io.StringIO()
        cols = ["event_id", "ts", "request_id", "actor", "action", "decision",
                "reason_codes", "primary_reason", "amount", "merchant",
                "approval_state", "policy_version", "engine_version", "event_hash"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for ev in self.events:
            d = ev.to_dict()
            d["reason_codes"] = ";".join(ev.reason_codes)
            w.writerow(d)
        if path:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(buf.getvalue())
            return path
        return buf.getvalue()

    # ── 旋转(克制: 归档不删除链) ──
    def rotate(self, keep: int = 1000, archive_path: str = "") -> dict:
        """归档最旧事件到文件, 保留最近 keep 条。归档保留哈希链, 新链从归档尾部续接。"""
        if len(self.events) <= keep:
            return {"archived": 0, "kept": len(self.events)}
        cut = len(self.events) - keep
        archived = self.events[:cut]
        tail = self.events[cut]
        path = archive_path or f"audit_archive_{int(time.time())}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(ev.to_jsonl() for ev in archived) + "\n")
        # 保留链: 新链起点 previous = 归档尾部的 hash(不重新生成 event_id)
        self.events = self.events[cut:]
        # 新链起点 = 归档最后一个事件的 hash(链无缝续接, 完整性不破坏)
        self._chain_root = archived[-1].event_hash
        self._by_request = {}
        for i, ev in enumerate(self.events):
            self._by_request.setdefault(ev.request_id, []).append(i)
        return {"archived": len(archived), "kept": len(self.events),
                "archive": path, "chain_continues_from": tail.event_hash}
