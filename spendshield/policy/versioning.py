# -*- coding: utf-8 -*-
"""
SpendShield V2 Policy Engine — 版本管理(纯文件快照, 不引数据库)

每次加载 policy 时快照到 .spendshield/policies/{version}.yaml,
每次评估记录 policy_version → 事后可复现「当时为什么放行」。
"""
from __future__ import annotations

import difflib
import os
import time
from typing import Optional


def _snapshot_dir(base_dir: Optional[str] = None) -> str:
    d = base_dir or os.path.join(os.getcwd(), ".spendshield", "policies")
    os.makedirs(d, exist_ok=True)
    return d


def snapshot(raw: dict, base_dir: Optional[str] = None) -> str:
    """保存 policy 快照, 返回文件路径。同名版本覆盖(版本即标识)。"""
    import json
    version = str(raw.get("version", "unknown"))
    d = _snapshot_dir(base_dir)
    path = os.path.join(d, f"{version}.yaml")
    # 统一存 JSON(避免 YAML 依赖), 但带时间戳记录首次保存
    if os.path.exists(path):
        pass  # 同版本重复加载不覆盖历史(保留首次)
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
    return path


def list_versions(base_dir: Optional[str] = None) -> list[str]:
    d = _snapshot_dir(base_dir)
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".yaml"))


def load_version(version: str, base_dir: Optional[str] = None) -> Optional[dict]:
    import json
    d = _snapshot_dir(base_dir)
    path = os.path.join(d, f"{version}.yaml")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def rollback(version: str, base_dir: Optional[str] = None) -> Optional[dict]:
    """回滚 = 读出该版本快照(由调用方决定是否写回主 policy 文件)"""
    return load_version(version, base_dir)


def diff(v1: str, v2: str, base_dir: Optional[str] = None) -> str:
    import json
    a = load_version(v1, base_dir)
    b = load_version(v2, base_dir)
    if a is None or b is None:
        return f"(missing version: {v1 if a is None else v2})"
    sa = json.dumps(a, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    sb = json.dumps(b, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    return "\n".join(difflib.unified_diff(sa, sb, fromfile=v1, tofile=v2, lineterm=""))
