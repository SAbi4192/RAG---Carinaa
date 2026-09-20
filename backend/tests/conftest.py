"""Shared fixtures for the Carinaa test suite.

ISOLATION COMES FIRST
---------------------
These tests must never touch the developer's real database, uploads or vector
index. Everything the app writes is redirected into a throwaway directory created
for this session.

The environment variables that do the redirecting are set *before* `app.core.config`
is imported, and that ordering is not cosmetic: `settings` is a module-level
singleton that reads the environment exactly once, at import time. Set them after
the first import and the app silently keeps using the real `data/` directory.

Provider keys are blanked for the same reason. An empty string makes `configured`
False, so no test can reach Gemini or Groq even if the developer has a populated
`.env` on disk.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

import pytest

# ---------------------------------------------------------------------------
# Redirect the app into a throwaway directory. MUST precede any `app.*` import.
# ---------------------------------------------------------------------------
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="carinaa-tests-"))
_DATA = _TMP_ROOT / "data"

os.environ["DATA_DIR"] = str(_DATA)
os.environ["UPLOAD_DIR"] = str(_DATA / "uploads")
os.environ["CHROMA_DIR"] = str(_DATA / "chroma")
os.environ["DATABASE_URL"] = f"sqlite:///{(_DATA / 'carinaa.db').as_posix()}"
os.environ["ENVIRONMENT"] = "test"
os.environ["SECRET_KEY"] = "test-only-secret-never-used-anywhere-real"

# Environment variables take precedence over the .env file in pydantic-settings,
# so these blank the real keys rather than being overridden by them.
os.environ["GEMINI_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""

# A valid, unguessable-looking password that satisfies password_problems():
# at least 8 characters, at least one letter, at least one digit.
TEST_PASSWORD = "CarinaaTest123"


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ANN001
    """Best-effort removal of the throwaway directory.

    Wrapped because some sandboxes intercept file deletion and raise SystemExit
    (a BaseException, so `except Exception` would not catch it). A failure to
    clean up a temp directory must never turn a green run into an error.
    """
    try:
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
    except (Exception, SystemExit):  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Create the schema once for the whole session."""
    from app.db.session import init_db

    init_db()


@pytest.fixture(scope="session")
def client():
    """A real HTTP client against the real ASGI app, lifespan included.

    Using TestClient as a context manager runs startup/shutdown, so the routers,
    middleware and exception handlers under test are the same ones the server uses.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db():
    """A request-scoped session against the throwaway database."""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def make_user(client) -> Callable[..., tuple[dict[str, str], int]]:
    """Register a fresh account and return ``(auth_headers, user_id)``.

    Every call creates a distinct user, which is what lets the isolation tests
    have a genuine second party to act as the attacker.
    """

    def _make(display_name: str = "Test User") -> tuple[dict[str, str], int]:
        # NOTE: example.com, not example.test - pydantic's EmailStr rejects the
        # special-use TLDs (.test, .invalid, .localhost) as invalid addresses.
        email = f"u{uuid.uuid4().hex[:12]}@example.com"
        response = client.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": TEST_PASSWORD,
                "display_name": display_name,
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]

    return _make


@pytest.fixture()
def make_workspace(client) -> Callable[..., int]:
    """Create a workspace for the given user and return its id."""

    def _make(headers: dict[str, str], name: str = "Test workspace") -> int:
        response = client.post(
            "/api/workspaces",
            json={"name": name, "description": "", "color": "violet"},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return _make


# ---------------------------------------------------------------------------
# Synthetic vectors
# ---------------------------------------------------------------------------
def unit_vector(seed: int, dim: int = 32):
    """A deterministic, L2-normalised vector.

    Synthetic vectors keep the isolation tests fast and exact: they need no
    embedding model, and because the vectors are identical the *only* thing that
    can separate two chunks is the workspace filter. That is precisely the
    property under test.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    vector = rng.normal(size=dim).astype(np.float32)
    return vector / float(np.linalg.norm(vector))


@pytest.fixture()
def store(tmp_path):
    """A VectorStore backed by a private Chroma directory.

    Deliberately NOT the singleton - each test gets its own collection so tests
    cannot interfere with each other.
    """
    from app.rag.vectorstore import VectorStore

    return VectorStore(path=tmp_path / "chroma", collection_name="test_chunks")


__all__ = ["unit_vector", "TEST_PASSWORD", "Any"]
