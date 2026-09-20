"""SQLAlchemy engine, session factory, and schema bootstrap."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_is_sqlite = settings.resolved_database_url.startswith("sqlite")

engine: Engine = create_engine(
    settings.resolved_database_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        """WAL + foreign keys.

        WAL lets the ingestion worker write while a chat request reads, which is
        what makes 'upload a document, immediately ask about it' feel instant.
        Foreign keys are OFF by default in SQLite - without this, cascade deletes
        silently leave orphan chunks behind.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for background work outside a request."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Idempotent.

    We use `create_all` rather than Alembic because the project is a teaching
    artefact: a student should be able to delete `data/carinaa.db` and get a
    clean, working database on the next start. `docs/11_database.md` explains the
    trade-off and what to do if the schema later needs to change in place.
    """
    from app.db import models  # noqa: F401  (ensure models are imported/registered)

    settings.ensure_directories()
    models.Base.metadata.create_all(bind=engine)
    logger.info("Database ready at %s", settings.resolved_database_url)


def healthcheck() -> dict:
    """Cheap liveness probe used by /api/health."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "url_scheme": settings.resolved_database_url.split(":", 1)[0]}
    except Exception as exc:  # pragma: no cover
        logger.error("Database healthcheck failed: %s", exc)
        return {"ok": False, "error": exc.__class__.__name__}
