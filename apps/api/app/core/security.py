from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash


password_hash = PasswordHash.recommended()

JWT_ALGORITHM = "HS256"


class AuthenticationError(Exception):
    """Raised when authentication credentials are invalid."""


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password cannot be empty.")

    return password_hash.hash(password)


def verify_password(
    plain_password: str,
    hashed_password: str,
) -> bool:
    if not plain_password or not hashed_password:
        return False

    try:
        return password_hash.verify(
            plain_password,
            hashed_password,
        )
    except Exception:
        return False


def create_access_token(
    user_id: uuid.UUID,
    secret_key: str,
    expires_minutes: int = 30,
) -> str:
    if not secret_key or len(secret_key) < 32:
        raise ValueError(
            "JWT secret must contain at least 32 characters."
        )

    if expires_minutes <= 0:
        raise ValueError(
            "Token expiration must be positive."
        )

    now = datetime.now(timezone.utc)

    payload = {
        "sub": str(user_id),
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(
            minutes=expires_minutes
        ),
        "type": "access",
    }

    return jwt.encode(
        payload,
        secret_key,
        algorithm=JWT_ALGORITHM,
    )


def decode_access_token(
    token: str,
    secret_key: str,
) -> uuid.UUID:
    if not secret_key or len(secret_key) < 32:
        raise AuthenticationError(
            "Authentication configuration is invalid."
        )

    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[JWT_ALGORITHM],
            options={
                "require": [
                    "sub",
                    "iat",
                    "nbf",
                    "exp",
                    "type",
                ],
            },
        )

        if payload.get("type") != "access":
            raise AuthenticationError(
                "Invalid token type."
            )

        return uuid.UUID(payload["sub"])

    except (
        InvalidTokenError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        raise AuthenticationError(
            "Invalid or expired access token."
        ) from exc


# ---------------------------------------------------------------------------
# Opaque tokens (refresh sessions, password reset, invitations)
# ---------------------------------------------------------------------------

def generate_opaque_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Multi-factor challenge token: proves the password step passed.
# ---------------------------------------------------------------------------

def create_mfa_challenge_token(
    user_id: uuid.UUID,
    secret_key: str,
    expires_minutes: int = 5,
) -> str:
    if not secret_key or len(secret_key) < 32:
        raise ValueError("JWT secret must contain at least 32 characters.")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "type": "mfa",
    }
    return jwt.encode(payload, secret_key, algorithm=JWT_ALGORITHM)


def decode_mfa_challenge_token(token: str, secret_key: str) -> uuid.UUID:
    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp", "type"]},
        )
        if payload.get("type") != "mfa":
            raise AuthenticationError("Invalid token type.")
        return uuid.UUID(payload["sub"])
    except (InvalidTokenError, ValueError, TypeError, KeyError) as exc:
        raise AuthenticationError("The sign-in challenge expired. Sign in again.") from exc


# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------

MIN_PASSWORD_LENGTH = 10


def password_policy_error(password: str, email: str | None = None) -> str | None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > 200:
        return "Use 200 characters or fewer."
    classes = sum(
        bool(re.search(pattern, password))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]")
    )
    if classes < 3:
        return "Mix at least three of: lowercase, uppercase, numbers and symbols."
    if email and email.split("@")[0].lower() in password.lower() and len(email.split("@")[0]) >= 4:
        return "Don't include your email name in the password."
    return None
