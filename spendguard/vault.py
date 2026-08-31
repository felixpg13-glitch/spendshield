# -*- coding: utf-8 -*-
"""
SpendGuard KeyVault — 密钥保险库(Fireblocks 最小版)

私钥/令牌加密落盘, 主密钥不落盘(环境变量持有)。
取密钥必须经过 SpendGuard 闸门(身份 + 意图审批), 每次取用留审计。

用法:
    from spendguard import SpendGuard, KeyVault

    # 首次: 生成主密钥, 放进环境变量 SPENDGUARD_MASTER_KEY(别落盘!)
    #   python -c "from spendguard import KeyVault; print(KeyVault.generate_key())"
    vault = KeyVault("vault.json", master_key=os.environ["SPENDGUARD_MASTER_KEY"])
    vault.store("mcd_sk", "sk_live_xxxx")          # 加密落盘, 文件里无明文

    guard = SpendGuard(key_vault=vault, approval="console")
    guard.register_agent("mcd_bot", whitelist=["mcd_sk"])   # 密钥名加白名单免问
    sk = guard.get_secret("mcd_sk", agent="mcd_bot")        # 过闸门才能取
"""
from __future__ import annotations

import json
import os
from typing import Optional

from cryptography.fernet import Fernet


class KeyVault:
    """加密密钥保险库: AES128-CBC + HMAC(Fernet), 主密钥不落盘"""

    def __init__(self, path: str = "spendguard_vault.json", master_key: Optional[str] = None):
        self.path = path
        if master_key is None:
            master_key = os.environ.get("SPENDGUARD_MASTER_KEY")
        if not master_key:
            raise ValueError(
                "需要主密钥: 传入 master_key 或设置环境变量 SPENDGUARD_MASTER_KEY "
                "(用 KeyVault.generate_key() 生成)"
            )
        if isinstance(master_key, str):
            master_key = master_key.encode()
        self._fernet = Fernet(master_key)
        self._data = self._load()

    @staticmethod
    def generate_key() -> str:
        """生成主密钥(仅打印一次, 放进环境变量, 别落盘)"""
        return Fernet.generate_key().decode()

    def store(self, name: str, value: str) -> None:
        """加密存储密钥(落盘文件里只有密文)"""
        if not name or not value:
            raise ValueError("name/value 不能为空")
        self._data[name] = self._fernet.encrypt(value.encode()).decode()
        self._save()

    def retrieve(self, name: str) -> str:
        """解密取回密钥(调用方负责先过 SpendGuard 闸门)"""
        if name not in self._data:
            raise KeyError(f"密钥不存在: {name}")
        return self._fernet.decrypt(self._data[name].encode()).decode()

    def names(self) -> list:
        return sorted(self._data.keys())

    def _load(self) -> dict:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
