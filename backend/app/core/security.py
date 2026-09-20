"""
Password hashing and JWT issuing/verification.

WHY THIS FILE EXISTS
--------------------
Section 53 of the spec: secure password hashing, login, logout, per-user data
isolation. Passwords are hashed with bcrypt (adaptive, salted, deliberately
slow). Sessions are stateless JWTs signed with the server secret.

The client is never trusted to choose its own authorization scope - see
`app/core/deps.py`, which derives `user_id` from the verified token only.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

import bcrypt
import jwt

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Return a bcrypt hash. Never store the plaintext."""
    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time comparison via bcrypt."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def password_problems(password: str) -> list[str]:
    """Return a list of unmet password requirements (empty list = acceptable)."""
    problems: list[str] = []
    if len(password) < 8:
        problems.append("at least 8 characters")
    if not re.search(r"[A-Za-z]", password):
        problems.append("at least one letter")
    if not re.search(r"\d", password):
        problems.append("at least one digit")
    return problems


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip()))


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def create_access_token(
    user_id: int,
    *,
    email: str,
    expires_minutes: int | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, dt.datetime]:
    """Create a signed access token. Returns (token, expiry)."""
    now = dt.datetime.now(dt.timezone.utc)
    expires_delta = dt.timedelta(minutes=expires_minutes or settings.access_token_expire_minutes)
    expire = now + expires_delta

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    if extra:
        payload.update(extra)

    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, expire


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Verify a token. Returns claims, or None if invalid/expired."""
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        logger.debug("Rejected expired token")
        return None
    except jwt.InvalidTokenError as exc:
        logger.debug("Rejected invalid token: %s", exc.__class__.__name__)
        return None


def token_user_id(claims: dict[str, Any]) -> int | None:
    try:
        return int(claims["sub"])
    except (KeyError, TypeError, ValueError):
        return None


def create_api_token() -> str:
    """Opaque random token, for future API access. Not used by the UI flow."""
    return secrets_token()


def secrets_token() -> str:
    import secrets

    return secrets.token_urlsafe(32)
