"""Authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.errors import EmailAlreadyRegistered, InvalidCredentials, WeakPassword
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    hash_password,
    is_valid_email,
    normalize_email,
    password_problems,
    verify_password,
)
from app.db.models import User, utcnow
from app.schemas.core import (
    LoginRequest,
    PreferencesUpdate,
    RegisterRequest,
    TokenOut,
    UserOut,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession) -> TokenOut:
    """Create an account and return a session token."""
    email = normalize_email(payload.email)
    if not is_valid_email(email):
        raise InvalidCredentials("That email address does not look valid.")

    problems = password_problems(payload.password)
    if problems:
        raise WeakPassword(
            "Your password must contain " + ", ".join(problems) + ".",
            detail={"requirements": problems},
        )

    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise EmailAlreadyRegistered()

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip() or email.split("@")[0],
        preferences={
            "theme": "dark",
            "language": "en",
            "default_ai_mode": "online",
            "default_top_k": 5,
            "default_rerank": False,
            "learning_mode": True,
            "developer_mode": False,
        },
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("Registered user %s", user.id)
    token, expires = create_access_token(user.id, email=user.email)
    return TokenOut(access_token=token, expires_at=expires, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, db: DbSession) -> TokenOut:
    """Exchange credentials for a session token."""
    email = normalize_email(payload.email)
    user = db.scalar(select(User).where(User.email == email))

    # Same error for "no such user" and "wrong password" so the endpoint cannot be
    # used to enumerate which emails have accounts.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise InvalidCredentials()

    if not user.is_active:
        raise InvalidCredentials("That account has been disabled.")

    user.last_login_at = utcnow()
    db.commit()
    db.refresh(user)

    token, expires = create_access_token(user.id, email=user.email)
    return TokenOut(access_token=token, expires_at=expires, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/preferences", response_model=UserOut)
def update_preferences(
    payload: PreferencesUpdate, user: CurrentUser, db: DbSession
) -> UserOut:
    """Merge partial preferences into the user's stored settings."""
    merged = dict(user.preferences or {})
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            merged[key] = value
    user.preferences = merged
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(_: CurrentUser) -> None:
    """Sessions are stateless JWTs, so logout is a client-side token discard.

    The endpoint exists so the frontend has a single, explicit place to call, and
    so the action is visible in the server log. If server-side revocation is ever
    needed, this is where a token denylist would go.
    """
    return None
