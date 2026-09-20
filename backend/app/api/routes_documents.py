"""
Document routes - upload, ingestion progress, inspection.

UPLOAD IS VALIDATED BEFORE ANYTHING IS STORED
---------------------------------------------
Extension, size and emptiness are checked first. A rejected upload leaves nothing
behind: no file on disk, no database row, no vectors. That matters because a
half-created document would show up in the Knowledge Base as a permanently
"pending" item with no way to complete.

PROGRESS IS STREAMED FROM REAL STATE
------------------------------------
`/documents/{id}/progress/stream` is a Server-Sent Events endpoint that reads the
Document row and emits it. It does not advance a timer. If parsing takes eight
seconds, the client sees "Parsing" for eight seconds.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import (
    CarinaaError,
    FileTooLarge,
    NotFound,
    ParseFailure,
    UnsupportedFileType,
)
from app.core.logging import get_logger
from app.core.storage import remove_stored_file
from app.db.models import Chunk, Document, Workspace
from app.db.session import session_scope
from app.ingestion import parsers as parser_registry
from app.ingestion.chunker import describe_chunks, preview as chunk_preview
from app.ingestion.pipeline import (
    document_progress,
    list_stage_definitions,
    reindex_document,
    start_ingestion_async,
    stale_documents,
)
from app.rag.vectorstore import get_vector_store
from app.schemas.core import (
    ChunkPreviewOut,
    ChunkPreviewRequest,
    DocumentListOut,
    DocumentOut,
    UploadResponse,
)

logger = get_logger(__name__)
router = APIRouter(tags=["documents"])

_CHUNK_SIZE = 1024 * 1024  # 1 MiB read blocks while streaming to disk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_document(db: DbSession, user: CurrentUser, document_id: int) -> Document:
    """Load a document, verifying ownership through its workspace.

    We join to Workspace and filter on `user_id`, so a document id from someone
    else's account resolves to nothing at all.
    """
    document = db.scalar(
        select(Document)
        .join(Workspace, Workspace.id == Document.workspace_id)
        .where(Document.id == document_id, Workspace.user_id == user.id)
    )
    if document is None:
        raise NotFound("That document does not exist.")
    return document


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
@router.get("/documents/supported-types")
def supported_types() -> dict:
    """What the uploader accepts, straight from the parser registry."""
    extensions = parser_registry.supported_extensions()
    return {
        "extensions": extensions,
        "labels": {ext: parser_registry.parser_label(ext) for ext in extensions},
        "max_upload_mb": settings.max_upload_mb,
        "stages": list_stage_definitions(),
        "notes": {
            "ocr": "Scanned or image-only PDFs are detected and flagged; Carinaa does not perform OCR.",
            "legacy_office": "Legacy .ppt and .xls files must be re-saved as .pptx / .xlsx.",
            "spreadsheets": (
                "Spreadsheet rows are indexed as labelled key/value pairs "
                "(e.g. 'Revenue: 1200') rather than as running prose."
            ),
        },
    }


@router.post("/documents/preview-chunks")
def preview_chunks(payload: ChunkPreviewRequest, _: CurrentUser) -> dict:
    """Run the real chunker over pasted text. Powers the Playground slider."""
    chunks = chunk_preview(
        payload.text, chunk_size=payload.chunk_size, chunk_overlap=payload.chunk_overlap
    )
    return {
        "chunk_size": payload.chunk_size,
        "chunk_overlap": payload.chunk_overlap,
        "chunks": chunks,
        "count": len(chunks),
        "note": (
            "This is the same chunker the ingestion pipeline uses. Nothing is stored and "
            "no model is called."
        ),
    }


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
@router.post(
    "/workspaces/{workspace_id}/documents",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    workspace: CurrentWorkspace,
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
) -> UploadResponse:
    """Accept a file, validate it, store it, and start ingestion."""
    filename = (file.filename or "").strip()
    if not filename:
        raise CarinaaError("The uploaded file has no name.", code="no_filename")

    if not parser_registry.is_supported(filename):
        raise UnsupportedFileType(
            f"'{Path(filename).suffix or filename}' is not a supported file type.",
            detail={"supported": parser_registry.supported_extensions()},
        )

    # ---- quota check ------------------------------------------------------
    existing = db.scalar(
        select(func.count()).select_from(Document).where(Document.workspace_id == workspace.id)
    )
    if int(existing or 0) >= settings.max_documents_per_workspace:
        raise CarinaaError(
            f"This workspace has reached its limit of "
            f"{settings.max_documents_per_workspace} documents.",
            code="document_limit",
            status_code=status.HTTP_409_CONFLICT,
        )

    # ---- stream to disk with a size cap -----------------------------------
    # We never read the whole upload into memory: a 2 GB "PDF" would otherwise
    # exhaust the server before we could reject it.
    settings.ensure_directories()
    extension = Path(filename).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{extension}"
    destination = settings.upload_dir / stored_name

    limit_bytes = settings.max_upload_mb * 1024 * 1024
    written = 0
    digest = hashlib.sha256()

    try:
        with destination.open("wb") as handle:
            while True:
                block = await file.read(_CHUNK_SIZE)
                if not block:
                    break
                written += len(block)
                if written > limit_bytes:
                    raise FileTooLarge(
                        f"That file is larger than the {settings.max_upload_mb} MB limit.",
                        detail={"limit_mb": settings.max_upload_mb},
                    )
                digest.update(block)
                handle.write(block)
    except Exception:
        # Any failure during upload leaves no trace on disk. Removal is best-effort
        # so that a cleanup problem can never mask the original upload error.
        remove_stored_file(stored_name, context="failed upload")
        raise
    finally:
        await file.close()

    if written == 0:
        remove_stored_file(stored_name, context="empty upload")
        raise ParseFailure("That file is empty.")

    # ---- create the document row -----------------------------------------
    document = Document(
        workspace_id=workspace.id,
        user_id=user.id,
        original_filename=filename,
        stored_filename=stored_name,
        file_type=extension.lstrip("."),
        size_bytes=written,
        checksum=digest.hexdigest(),
        status="pending",
        stage="uploading",
        progress=0.0,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    # ---- start ingestion ---------------------------------------------------
    start_ingestion_async(document.id)
    logger.info(
        "Uploaded %s (%.2f MB) to workspace %s as document %s",
        filename,
        written / (1024 * 1024),
        workspace.id,
        document.id,
    )

    return UploadResponse(
        document=DocumentOut.model_validate(document),
        message=f"'{filename}' was uploaded and is now being processed.",
        ingestion_started=True,
    )


# ---------------------------------------------------------------------------
# Listing & detail
# ---------------------------------------------------------------------------
@router.get("/workspaces/{workspace_id}/documents", response_model=DocumentListOut)
def list_documents(
    workspace: CurrentWorkspace,
    db: DbSession,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> DocumentListOut:
    statement = select(Document).where(Document.workspace_id == workspace.id)
    if status_filter:
        statement = statement.where(Document.status == status_filter)
    statement = statement.order_by(Document.created_at.desc()).limit(limit)

    rows = db.scalars(statement).all()
    return DocumentListOut(
        documents=[DocumentOut.model_validate(row) for row in rows], total=len(rows)
    )


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, user: CurrentUser, db: DbSession) -> DocumentOut:
    return DocumentOut.model_validate(_load_document(db, user, document_id))


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: int, user: CurrentUser, db: DbSession) -> None:
    """Delete a document, its vectors, and its stored file.

    The ORDER is deliberate, and so is the failure policy:

    1. Vectors first. If this fails we raise and nothing else has changed, so the
       document stays fully usable. A deleted row with live vectors would be
       unreachable data, which is worse than a failed delete.

    2. Commit the database change. This is the point of no return - once the row is
       gone, the document is gone.

    3. Remove the file LAST, best-effort. The stored file is a convenience copy (the
       text lives in the chunks, the vectors live in the index), so a file that
       cannot be removed is a housekeeping problem - not a reason to fail a delete
       that already succeeded, and not a reason to leave the database inconsistent.

    See `app/core/storage.py` for why step 3 swallows more than `OSError`.
    """
    document = _load_document(db, user, document_id)

    get_vector_store().delete_document(document.id)

    stored_filename = document.stored_filename

    db.delete(document)
    db.commit()
    logger.info("Deleted document %s", document_id)

    remove_stored_file(stored_filename, context=f"document {document_id}")


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------
@router.get("/documents/{document_id}/progress")
def get_progress(document_id: int, user: CurrentUser, db: DbSession) -> dict:
    _load_document(db, user, document_id)
    return document_progress(db, document_id)


@router.get("/documents/{document_id}/progress/stream")
async def stream_progress(
    document_id: int, request: Request, user: CurrentUser
) -> StreamingResponse:
    """Server-Sent Events carrying the document's real ingestion state.

    We re-read the row on every tick rather than subscribing to a message bus: the
    database IS the state, so there is exactly one source of truth and no way for
    the stream to disagree with the UI.
    """
    # Verify ownership once, up front, using a short-lived session.
    with session_scope() as check_db:
        document = check_db.scalar(
            select(Document)
            .join(Workspace, Workspace.id == Document.workspace_id)
            .where(Document.id == document_id, Workspace.user_id == user.id)
        )
        if document is None:
            raise NotFound("That document does not exist.")

    async def event_stream():
        terminal = {"ready", "failed"}
        idle_ticks = 0
        max_ticks = 900  # ~5 minutes at 350 ms per tick

        for _ in range(max_ticks):
            if await request.is_disconnected():
                break

            with session_scope() as tick_db:
                snapshot = document_progress(tick_db, document_id)

            if not snapshot.get("found"):
                yield f"event: gone\ndata: {json.dumps({'document_id': document_id})}\n\n"
                break

            yield f"data: {json.dumps(snapshot)}\n\n"

            if snapshot.get("status") in terminal:
                yield "event: done\ndata: {}\n\n"
                break

            # Keep the connection warm while nothing changes.
            idle_ticks += 1
            if idle_ticks % 30 == 0:
                yield ": keep-alive\n\n"

            await asyncio.sleep(0.35)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Chunks & maintenance
# ---------------------------------------------------------------------------
@router.get("/documents/{document_id}/chunks", response_model=list[ChunkPreviewOut])
def list_chunks(
    document_id: int,
    user: CurrentUser,
    db: DbSession,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[ChunkPreviewOut]:
    """The chunks that were actually indexed for this document."""
    _load_document(db, user, document_id)

    rows = db.scalars(
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index)
        .offset(offset)
        .limit(limit)
    ).all()

    return [
        ChunkPreviewOut(
            id=row.id,
            chunk_index=row.chunk_index,
            content=row.content,
            char_start=row.char_start,
            char_end=row.char_end,
            token_estimate=row.token_estimate,
            block_type=row.block_type,
            doc_metadata=row.doc_metadata or {},
        )
        for row in rows
    ]


@router.get("/documents/{document_id}/chunk-stats")
def chunk_stats(document_id: int, user: CurrentUser, db: DbSession) -> dict:
    """Chunking statistics for the Document Viewer page."""
    _load_document(db, user, document_id)
    rows = db.scalars(
        select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
    ).all()
    if not rows:
        return {"count": 0}

    lengths = [len(row.content) for row in rows]
    return {
        "count": len(rows),
        "characters": {
            "total": sum(lengths),
            "min": min(lengths),
            "max": max(lengths),
            "mean": round(sum(lengths) / len(lengths), 1),
        },
        "block_types": _count_by(rows, "block_type"),
        "pages": sorted({r.doc_metadata.get("page_number") for r in rows if r.doc_metadata.get("page_number")}),
        "sections": sorted(
            {str(r.doc_metadata.get("section")) for r in rows if r.doc_metadata.get("section")}
        )[:40],
    }


def _count_by(rows, attribute: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(getattr(row, attribute, "unknown"))
        counts[key] = counts.get(key, 0) + 1
    return counts


@router.post("/documents/{document_id}/reindex")
def reindex(document_id: int, user: CurrentUser, db: DbSession) -> dict:
    """Re-embed and re-index from the chunks already in SQLite.

    Needed after changing the embedding model: the text is still in SQLite, so we
    do not re-parse the original file and do not need it on disk.
    """
    document = _load_document(db, user, document_id)
    ok = reindex_document(document.id)
    return {
        "document_id": document.id,
        "success": ok,
        "embedding_model": settings.embedding_model,
        "message": (
            "Re-indexed with the current embedding model."
            if ok
            else "Re-indexing failed. Check the server log for details."
        ),
    }


@router.get("/workspaces/{workspace_id}/stale-documents")
def list_stale(workspace: CurrentWorkspace, db: DbSession) -> dict:
    """Documents whose vectors were built with a different embedding model."""
    stale = stale_documents(db, workspace.id)
    return {
        "current_model": settings.embedding_model,
        "count": len(stale),
        "documents": stale,
        "note": (
            "A vector is only comparable with vectors from the same model. Re-index "
            "these documents to make them retrievable again."
        ),
    }
