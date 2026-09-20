"""
FastAPI dependencies - the single place authorization is decided.

WHY THIS FILE EXISTS
--------------------
Spec section 18: "The client must not be trusted to choose its own authorization
scope."

The browser never sends a `user_id`. It sends a signed token. `get_current_user`
verifies that token and returns the *server's* idea of who you are. Every route
that touches a workspace then goes through `require_workspace`, which loads the
workspace **filtered by owner**. If you are not the owner, you get a 404 - not a
403 - so an attacker cannot even probe which workspace IDs exist.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AuthError, Forbidden, NotFound
from app.core.security import decode_access_token, token_user_id
from app.db.models import User, Workspace
from app.db.session import get_db

DbSession = Annotated[Session, Depends(get_db)]


def _bearer_token(request: Request, authorization: Annotated[str | None, Header()] = None) -> str | None:
    """Extract a bearer token from the Authorization header.

    A cookie fallback is intentionally NOT provided: tokens live in memory /
    sessionStorage on the client, which keeps CSRF surface minimal for this
    project's threat model.
    """
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    # Allow ?token= only for the SSE/EventSource path, which cannot set headers.
    token = request.query_params.get("token")
    return token.strip() if token else None


def get_current_user(
    db: DbSession,
    token: Annotated[str | None, Depends(_bearer_token)],
) -> User:
    """Resolve the authenticated user, or raise 401."""
    if not token:
        raise AuthError("Missing authentication token.")

    claims = decode_access_token(token)
    if not claims:
        raise AuthError("Your session has expired. Please sign in again.")

    user_id = token_user_id(claims)
    if user_id is None:
        raise AuthError("Malformed authentication token.")

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthError("That account is no longer active.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_workspace(
    workspace_id: int,
    db: DbSession,
    user: CurrentUser,
) -> Workspace:
    """Load a workspace ONLY if it belongs to the current user.

    Returns 404 (not 403) on a foreign workspace so IDs are not enumerable.
    """
    workspace = db.scalar(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.user_id == user.id)
    )
    if workspace is None:
        raise NotFound("That workspace does not exist.")

    # Defence in depth: re-assert ownership after loading.
    if workspace.user_id != user.id:
        raise Forbidden("You do not have access to that workspace.")
    return workspace


CurrentWorkspace = Annotated[Workspace, Depends(require_workspace)]


def optional_user(
    db: DbSession,
    token: Annotated[str | None, Depends(_bearer_token)],
) -> User | None:
    """For routes that behave differently when signed in (e.g. landing stats)."""
    if not token:
        return None
    claims = decode_access_token(token)
    if not claims:
        return None
    user_id = token_user_id(claims)
    return db.get(User, user_id) if user_id else None
