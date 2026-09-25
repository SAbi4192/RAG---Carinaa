"""Health and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.db.session import healthcheck as db_healthcheck
from app.llm.adapter import get_llm_adapter
from app.rag.embeddings import get_embedding_service
from app.rag.vectorstore import get_vector_store

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """Cheap liveness check. Does not touch the model files."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "tagline": settings.app_tagline,
    }


@router.get("/health/deep")
def deep_health() -> dict:
    """Component-level readiness.

    This is what the Settings page and the demo use to prove the stack is up. Each
    component reports honestly, including when it is NOT ready - a deep health
    check that always says "ok" is worse than useless.
    """
    database = db_healthcheck()
    vector_store = get_vector_store().health()

    embedding_service = get_embedding_service()
    embeddings = {
        "ok": embedding_service.is_ready,
        "model": embedding_service.model_name,
        "dimension": embedding_service.dimension if embedding_service.is_ready else 0,
        "loaded": embedding_service.is_ready,
        "local": True,
        "note": (
            "Loaded on first use. Run scripts/fetch_models.py if this reports an error."
        ),
    }

    adapter = get_llm_adapter()
    modes = adapter.mode_status()
    # The deep health payload is public (no auth) and user-facing. Engine identity
    # is reported by ROLE only - cloud vendor names, model IDs and key-variable
    # names are developer information and belong in server logs, not here.
    from app.core.sanitize import public_engines

    engines = public_engines()
    online_available = engines["modes"]["online"]["available"]
    offline_available = engines["modes"]["offline"]["available"]

    overall = "ok"
    if not database.get("ok") or not vector_store.get("ok"):
        overall = "degraded"
    elif not online_available and not offline_available:
        overall = "degraded"

    return {
        "status": overall,
        "components": {
            "database": database,
            "vector_store": vector_store,
            "embeddings": embeddings,
            "engines": engines["engines"],
            "modes": engines["modes"],
        },
        "configuration": {
            "remote_engine_configured": online_available,
            "local_model_present": bool(
                next(e for e in engines["engines"] if e["role"] == "offline")["configured"]
            ),
            "note": (
                "Only whether the answer engines are configured is reported. "
                "Credential values are never exposed."
            ),
        },
    }
