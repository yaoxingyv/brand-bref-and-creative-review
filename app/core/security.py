# -*- coding: utf-8 -*-
"""
核心层：鉴权与密码安全
================================================================
选型说明（有意为之的依赖取舍）：
  · 密码哈希用标准库 hashlib.pbkdf2_hmac，不引入 passlib/bcrypt。
    原因：bcrypt 在 Windows 上常因编译环境失败，且本项目不需要
    跨语言兼容的哈希格式。pbkdf2_hmac 是 NIST 认可方案，够用。
  · JWT 用 PyJWT，签发/校验都在本文件，业务层只见 create_access_token
    与 decode_token 两个函数。
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt

from app.core.config import settings

# PBKDF2 迭代次数。越高越安全但越慢；10 万次在现代机器上约几十毫秒。
_PBKDF2_ITERATIONS = 100_000


def hash_password(password: str) -> str:
    """生成密码哈希。格式：pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>"""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码。解析失败（旧格式/损坏数据）一律返回 False，不抛异常。"""
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        # 用 hmac.compare_digest 防时序侧信道
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def create_access_token(subject: str, role: str, expires_minutes: Optional[int] = None) -> str:
    """签发 JWT。payload 里带 role，入口层据此做角色拦截。"""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.JWT_EXPIRE_MINUTES
    )
    payload: Dict[str, Any] = {
        "sub": str(subject),   # 用户 ID
        "role": role,          # employee | admin
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """解码 JWT。任何异常（过期/篡改/格式错）都返回 None，由调用方转成 401。"""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def random_token(nbytes: int = 32) -> str:
    """生成随机串（预留：刷新令牌、上传签名等场景）。"""
    return secrets.token_hex(nbytes)
