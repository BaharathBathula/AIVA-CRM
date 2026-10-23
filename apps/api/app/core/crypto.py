"""Symmetric encryption for secrets and encrypted fields.

Values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256). The key comes
from ``FIELD_ENCRYPTION_KEY`` when set, otherwise it is derived from the JWT
secret so a fresh install works without extra configuration. Rotating the
key makes previously encrypted values unreadable, so set
``FIELD_ENCRYPTION_KEY`` explicitly in production and keep it safe.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

PREFIX = "enc:v1:"


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    material = settings.field_encryption_key or f"aiva-field-encryption:{settings.jwt_secret_key}"
    try:
        # Accept a ready-made Fernet key as-is.
        if len(material) == 44:
            return Fernet(material.encode())
    except ValueError:
        pass
    digest = hashlib.sha256(material.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str | None) -> str | None:
    if value is None or value == "":
        return value
    if is_encrypted(value):
        return value
    return PREFIX + _fernet().encrypt(str(value).encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not value or not is_encrypted(value):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        return None


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def mask(value: str | None) -> str | None:
    if value in (None, ""):
        return value
    text = str(value)
    return "•" * 6 + text[-2:] if len(text) > 4 else "•" * 6


def new_token(prefix: str = "", nbytes: int = 24) -> str:
    return prefix + secrets.token_urlsafe(nbytes)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hmac_sha256(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
