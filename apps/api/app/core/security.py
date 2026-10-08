from __future__ import annotations

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
