"""RFC 6238 time-based one-time passwords (compatible with authenticator apps)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code_at(secret: str, counter: int, digits: int = 6) -> str:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def current_code(secret: str, at: float | None = None, step: int = 30) -> str:
    return _code_at(secret, int((at if at is not None else time.time()) // step))


def verify_code(secret: str, code: str, window: int = 1, step: int = 30) -> bool:
    if not secret or not code:
        return False
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6:
        return False
    counter = int(time.time() // step)
    return any(
        hmac.compare_digest(_code_at(secret, counter + delta), code)
        for delta in range(-window, window + 1)
    )


def provisioning_uri(secret: str, account_name: str, issuer: str = "AIVA CRM") -> str:
    label = quote(f"{issuer}:{account_name}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
