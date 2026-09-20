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
    providers = [status.as_dict() for status in adapter.provider_statuses()]

    overall = "ok"
    if not database.get("ok") or not vector_store.get("ok"):
        overall = "degraded"
    elif not modes["online"]["available"] and not modes["offline"]["available"]:
        overall = "degraded"

    return {
        "status": overall,
        "components": {
            "database": database,
            "vector_store": vector_store,
            "embeddings": embeddings,
            "providers": providers,
            "modes": modes,
        },
        "secrets": {
            "gemini_configured": settings.gemini_configured,
            "groq_configured": settings.groq_configured,
            "note": "Only whether a key is present is reported. Values are never exposed.",
        },
    }
