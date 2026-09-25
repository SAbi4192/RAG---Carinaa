"""
Settings routes - what the operator and the demo can see and change.

WHAT IS SAFE TO EXPOSE
----------------------
Everything here is deliberately non-sensitive: model names, retrieval parameters,
which providers are configured, whether the local model file is present.

WHAT IS NEVER EXPOSED
---------------------
API keys, the JWT secret, absolute paths outside the project, and the contents of
the .env file. `Settings.redacted_summary()` is the single place that decides what
crosses the boundary, so there is one file to audit rather than twenty endpoints.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.core.sanitize import local_model_name, public_engines
from app.db.models import Chunk, Document, Message, QueryLog, Workspace
from app.features.languages import as_list as languages_as_list
from app.ingestion.pipeline import list_stage_definitions
from app.llm.adapter import get_llm_adapter
from app.rag.embeddings import get_embedding_service
from app.rag.grounding import STATUS_LABELS
from app.rag.reranker import get_reranker
from app.rag.trace import stage_definitions
from app.rag.vectorstore import get_vector_store

logger = get_logger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def get_configuration(_: CurrentUser) -> dict:
    """The redacted configuration summary shown on the Settings page."""
    return settings.redacted_summary()


@router.get("/rag")
def rag_configuration(_: CurrentUser) -> dict:
    """The retrieval knobs, with a plain-language explanation of each."""
    return {
        "chunking": {
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "min_chunk_chars": settings.min_chunk_chars,
            "max_chunk_chars": settings.max_chunk_chars,
            "strategy": "structure-aware recursive splitting, overlapped by whole block",
            "explanation": (
                "Chunk size is the target number of characters per retrievable unit. "
                "Overlap repeats the tail of one chunk at the start of the next so a fact "
                "spanning a boundary is not lost from both. We overlap by whole blocks so "
                "every character still maps to exactly one page or row."
            ),
        },
        "retrieval": {
            "top_k": settings.top_k,
            "candidate_k": settings.candidate_k,
            "min_relevance_score": settings.min_relevance_score,
            "max_context_chars": settings.max_context_chars,
            "explanation": (
                "candidate_k chunks are fetched from the vector index; the best top_k of "
                "them are sent to the language model. Fetching more than you use costs "
                "almost nothing and gives re-ranking room to work."
            ),
        },
        "rerank": {
            **get_reranker().info(),
            "explanation": (
                "Re-ranking reorders candidates that retrieval already found. It cannot "
                "recover evidence vector search missed, which is why it is off by default."
            ),
        },
        "grounding": {
            "enabled": settings.grounding_enabled,
            "require_citations": settings.require_citations,
            "min_overlap": settings.grounding_min_overlap,
            "statuses": STATUS_LABELS,
            "explanation": (
                "Grounding checks whether the answer is supported by the retrieved "
                "evidence. It measures the answer-to-evidence relationship, not whether "
                "the document itself is correct."
            ),
        },
        "embeddings": get_embedding_service().info(),
        "pipeline_stages": stage_definitions(),
        "ingestion_stages": list_stage_definitions(),
    }


@router.get("/providers")
def providers(_: CurrentUser) -> dict:
    """Engine status expressed as ROLES: primary remote engine, backup remote
    engine, local model. Cloud vendor identity is never sent to the browser; the
    server still knows and logs it (app.core.sanitize is the single boundary).
    """
    return public_engines()


# No /providers/models route: it enumerated live cloud model IDs, which is vendor
# identity, and nothing in the frontend or the tests called it. The adapter keeps
# `list_provider_models()` for operator use (scripts, logs); it is simply not a
# public endpoint any more.


@router.post("/local-model/load")
async def load_local_model(_: CurrentUser) -> dict:
    """Load the GGUF into memory ahead of the demo."""
    adapter = get_llm_adapter()
    loaded = await asyncio.to_thread(adapter.local.load)
    return {
        "loaded": loaded,
        "info": adapter.local.info(),
        "message": (
            "The local model is loaded and ready for Offline mode."
            if loaded
            else "The local model could not be loaded. Check the Settings page for details."
        ),
    }


@router.post("/local-model/unload")
async def unload_local_model(_: CurrentUser) -> dict:
    """Free the local model's memory."""
    adapter = get_llm_adapter()
    adapter.local.unload()
    return {"loaded": False, "message": "The local model was unloaded from memory."}


@router.get("/system")
def system_stats(user: CurrentUser, db: DbSession) -> dict:
    """Real, per-user system statistics. No invented numbers."""
    workspace_ids = select(Workspace.id).where(Workspace.user_id == user.id)

    documents = db.scalar(
        select(func.count()).select_from(Document).where(Document.user_id == user.id)
    )
    chunks = db.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.workspace_id.in_(workspace_ids))
    )
    messages = db.scalar(
        select(func.count())
        .select_from(Message)
        .join(Workspace, Workspace.id == Message.conversation_id, isouter=True)
        .where(Workspace.user_id == user.id)
    )
    queries = db.scalar(
        select(func.count()).select_from(QueryLog).where(QueryLog.user_id == user.id)
    )

    vector_store = get_vector_store()

    return {
        "documents": int(documents or 0),
        "chunks": int(chunks or 0),
        "messages": int(messages or 0),
        "queries": int(queries or 0),
        "vector_store": vector_store.health(),
        "embedding_model": settings.embedding_model,
        "database": settings.resolved_database_url.split(":", 1)[0],
        "storage": {
            "uploads_dir": "data/uploads",
            "vector_dir": "data/chroma",
            "note": "Paths are relative to the project root.",
        },
    }


@router.get("/languages")
def languages(_: CurrentUser) -> dict:
    return {"languages": languages_as_list(), "default": "en"}
