"""对称加解密：大模型 API Key 等敏感值加密落库，可解密还原去调接口。

设计要点：
- 用 Fernet（AES-128-CBC + HMAC）对称加密，可逆——Key 需还原成明文发
  Authorization 头，故不能用哈希（不可逆）。
- 主密钥从环境变量 `AUTOTEST_SECRET_KEY` 注入（部署时由运维/平台提供），
  代码内不保留任何固定密钥；未设置时抛错并给出指引。
- 密文带前缀 enc:v1: 便于识别与幂等；encrypt 对已加密值原样返回（幂等）。
- decrypt 只接受带前缀的密文：无前缀的值视为不合法，返回空串（不再兼容明文
  落库的历史遗留，避免敏感值明文存库）。
- 跨机共享同一数据库时，各机器需设置相同的环境变量才能互相解密。
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

# 主密钥环境变量名
KEY_ENV = "AUTOTEST_SECRET_KEY"

# 密文前缀：标识版本，便于识别与幂等
_PREFIX = "enc:v1:"

# 模块级缓存的 Fernet 实例
_fernet: Optional[Fernet] = None


def _derive_key(raw: str) -> bytes:
    """把任意字符串主密钥经 SHA-256 派生为合法 Fernet key（urlsafe base64, 32 字节）。"""
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet() -> Fernet:
    """获取缓存的 Fernet 实例（主密钥来自环境变量 AUTOTEST_SECRET_KEY）。"""
    global _fernet
    if _fernet is None:
        raw = os.environ.get(KEY_ENV, "")
        if not raw:
            raise RuntimeError(
                f"未设置环境变量 {KEY_ENV}：无法加/解密落库的敏感值。"
                "请在启动环境中注入（任意足够长的随机串），"
                "各机器共用同一数据库时须保持一致。"
            )
        _fernet = Fernet(_derive_key(raw))
    return _fernet


def encrypt(plaintext: str) -> str:
    """加密明文，返回带 enc:v1: 前缀的密文。

    空串返回空串；已是密文（带前缀）则原样返回（幂等）。
    """
    if not plaintext:
        return ""
    if plaintext.startswith(_PREFIX):
        return plaintext
    token = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return _PREFIX + token


def decrypt(value: str) -> str:
    """解密密文，返回明文。

    空串返回空串；无前缀的值不视为合法密文，返回空串（不兼容明文落库）；
    解密失败（主密钥不匹配/密文损坏）返回空串——让调用方明确得到"key 未设置"，
    而非崩溃或拿到脏数据。
    """
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        return ""
    token = value[len(_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        print("[crypto] 解密失败：主密钥不匹配或密文损坏，返回空串")
        return ""
