"""
Ingestion pipeline - upload to ready.

THE PIPELINE
------------
    Upload
      |
    Validate      is the type supported? is it too big? is it empty?
      |
    Parse         format-specific parser reads the file          (pypdf, python-docx, ...)
      |
    Extract       parser emits blocks with provenance metadata
      |
    Normalise     drop empties, duplicates and repeated boilerplate
      |
    Chunk         structure-aware recursive split with overlap
      |
    Embed         local ONNX model turns each chunk into a vector
      |
    Store         vectors -> ChromaDB,  text + metadata -> SQLite
      |
    Ready         the workspace can now answer questions about this file

REAL PROGRESS, NOT A TIMER (spec section 7)
-------------------------------------------
"Parse: 5 of 8" is computed by counting files, and "Embedding: 62%" is computed
from the number of batches actually completed. Nothing here advances on a
`setTimeout`. If parsing takes 8 seconds, the bar sits at "Parsing" for 8 seconds.
That honesty is a deliberate requirement: a progress bar that lies makes every
other number in the application untrustworthy.

FAILURE HANDLING (spec section 56)
----------------------------------
Every stage is wrapped. On failure we record the stage that failed, store a
redacted message on the Document row, set status='failed', and clean up any
partial vectors so the workspace never contains half a document.
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import CarinaaError, ParseFailure
from app.core.logging import get_logger, safe_error_message
from app.db.models import Chunk, Document, utcnow
from app.db.session import session_scope
from app.ingestion import parsers as parser_registry
from app.ingestion.chunker import chunk_document, describe_chunks
from app.ingestion.normalizer import normalize_document, summarize, validate_document
from app.ingestion.types import NormalizedDocument
from app.rag.embeddings import get_embedding_service
from app.rag.vectorstore import get_vector_store

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Stage definitions: (name, cumulative progress at completion, human label)
# ---------------------------------------------------------------------------
STAGES: tuple[tuple[str, float, str], ...] = (
    ("uploading", 0.03, "Uploading"),
    ("parsing", 0.20, "Parsing"),
    ("extracting", 0.34, "Extracting"),
    ("normalizing", 0.46, "Normalizing"),
    ("chunking", 0.58, "Chunking"),
    ("embedding", 0.92, "Embedding"),
    ("indexing", 0.99, "Indexing"),
    ("ready", 1.00, "Ready"),
)

STAGE_LABELS: dict[str, str] = {name: label for name, _, label in STAGES}

# One ingestion at a time per document. Prevents a double-click from running the
# same file through the pipeline twice and duplicating every vector.
_active_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _document_lock(document_id: int) -> threading.Lock:
    with _locks_guard:
        if document_id not in _active_locks:
            _active_locks[document_id] = threading.Lock()
        return _active_locks[document_id]


@dataclass
class StageResult:
    stage: str
    duration_ms: int
    detail: dict[str, Any]


class IngestionPipeline:
    """Runs one document through the full ingestion pipeline."""

    def __init__(self, document_id: int) -> None:
        self.document_id = document_id
        self.started_at = time.perf_counter()
        self.stages: list[StageResult] = []
        self.timings: dict[str, int] = {}

    # ------------------------------------------------------------------ public
    def run(self) -> bool:
        """Execute the pipeline. Returns True on success."""
        lock = _document_lock(self.document_id)
        if not lock.acquire(blocking=False):
            logger.warning("Ingestion already running for document %s", self.document_id)
            return False

        try:
            return self._run_locked()
        finally:
            lock.release()

    # ----------------------------------------------------------------- internal
    def _run_locked(self) -> bool:
        try:
            with session_scope() as db:
                document = db.get(Document, self.document_id)
                if document is None:
                    logger.error("Document %s vanished before ingestion", self.document_id)
                    return False

                document.status = "processing"
                document.error_message = None
                document.stage = "uploading"
                document.progress = 0.02
                db.commit()

            # =============================================================
            # PARSE
            # =============================================================
            with session_scope() as db:
                document = db.get(Document, self.document_id)
                assert document is not None
                path = settings.upload_dir / document.stored_filename
                original_name = document.original_filename

                self._mark(db, document, "parsing", 0.05)

            parsed = self._timed(
                "parsing",
                lambda: parser_registry.parse_file(path, self.document_id, original_name),
            )

            # =============================================================
            # EXTRACT - block assembly + format-level metadata
            # =============================================================
            with session_scope() as db:
                document = db.get(Document, self.document_id)
                assert document is not None
                self._mark(db, document, "extracting", 0.22)

            extracted: NormalizedDocument = self._timed(
                "extracting", lambda: self._extract(parsed)
            )

            # =============================================================
            # NORMALISE
            # =============================================================
            with session_scope() as db:
                document = db.get(Document, self.document_id)
                assert document is not None
                self._mark(db, document, "normalizing", 0.36)

            normalized, report = self._timed(
                "normalizing", lambda: normalize_document(extracted)
            )

            if normalized.is_empty:
                raise ParseFailure(
                    "After cleaning, this document contained no usable text. "
                    "It may be a scanned image or an empty file."
                )

            # =============================================================
            # CHUNK
            # =============================================================
            with session_scope() as db:
                document = db.get(Document, self.document_id)
                assert document is not None
                self._mark(db, document, "chunking", 0.48)

            drafts = self._timed(
                "chunking",
                lambda: chunk_document(
                    normalized,
                    chunk_size=settings.chunk_size,
                    chunk_overlap=settings.chunk_overlap,
                    min_chunk_chars=settings.min_chunk_chars,
                    max_chunk_chars=settings.max_chunk_chars,
                ),
            )

            if not drafts:
                raise ParseFailure("No chunks could be produced from this document.")

            chunk_stats = describe_chunks(drafts)

            # ---- persist chunks (text + metadata) BEFORE embedding ---------
            # Writing them first means a crash during embedding leaves an honest
            # 'failed' document with visible chunks, rather than a mystery.
            with session_scope() as db:
                document = db.get(Document, self.document_id)
                assert document is not None
                db.query(Chunk).filter(Chunk.document_id == self.document_id).delete()
                db.flush()

                embedding_model = settings.embedding_model
                for draft in drafts:
                    db.add(
                        Chunk(
                            document_id=self.document_id,
                            workspace_id=document.workspace_id,
                            chunk_index=draft.chunk_index,
                            content=draft.content,
                            char_start=draft.char_start,
                            char_end=draft.char_end,
                            token_estimate=draft.token_estimate,
                            block_type=draft.block_type,
                            doc_metadata=draft.metadata,
                            vector_id=draft.vector_id(self.document_id),
                            embedding_model=embedding_model,
                        )
                    )

                document.chunk_count = len(drafts)
                document.char_count = sum(d.length for d in drafts)
                document.token_estimate = sum(d.token_estimate for d in drafts)
                document.page_count = normalized.page_count
                document.embedding_model = embedding_model
                db.commit()

            # =============================================================
            # EMBED + INDEX
            # =============================================================
            embedding_service = get_embedding_service()
            dimension = embedding_service.dimension

            with session_scope() as db:
                document = db.get(Document, self.document_id)
                assert document is not None
                document.embedding_dim = dimension
                self._mark(db, document, "embedding", 0.58)

            vectors = self._embed_with_progress([d.content for d in drafts])

            with session_scope() as db:
                document = db.get(Document, self.document_id)
                assert document is not None
                self._mark(db, document, "indexing", 0.93)

            store = get_vector_store()
            store.delete_document(self.document_id)  # idempotent re-index

            # Read the authoritative workspace id from the Document row, not from
            # the request. The client never gets to decide which workspace its
            # vectors land in.
            workspace_id = self._workspace_id()

            self._timed(
                "indexing",
                lambda: store.add_chunks(
                    workspace_id=workspace_id,
                    document_id=self.document_id,
                    vector_ids=[d.vector_id(self.document_id) for d in drafts],
                    contents=[d.content for d in drafts],
                    embeddings=vectors,
                    metadatas=[d.metadata for d in drafts],
                ),
            )

            # =============================================================
            # READY
            # =============================================================
            with session_scope() as db:
                document = db.get(Document, self.document_id)
                assert document is not None
                stats = summarize(normalized, report)
                document.doc_metadata = {**normalized.doc_metadata, "stats": stats}
                document.page_count = normalized.page_count or document.page_count
                document.stage = "ready"
                document.status = "ready"
                document.progress = 1.0
                document.processed_at = utcnow()
                document.stage_timings = self.timings
                document.error_message = None
                db.commit()

            total_ms = int((time.perf_counter() - self.started_at) * 1000)
            logger.info(
                "Ingestion complete: document=%s chunks=%s vectors=%s in %dms",
                self.document_id,
                len(drafts),
                len(drafts),
                total_ms,
            )
            logger.debug("Chunk stats: %s", chunk_stats)
            return True

        except CarinaaError as exc:
            self._fail(exc.message, exc.code)
            return False
        except Exception as exc:
            logger.exception("Ingestion failed for document %s", self.document_id)
            self._fail(
                "Processing failed while reading this document. "
                "It may be corrupted, password-protected, or in an unexpected layout.",
                "ingestion_failed",
            )
            logger.debug("Traceback: %s", traceback.format_exc(limit=3))
            logger.debug("Raw error: %s", safe_error_message(exc))
            return False

    # ------------------------------------------------------------------ helpers
    def _workspace_id(self) -> int:
        with session_scope() as db:
            document = db.get(Document, self.document_id)
            return int(document.workspace_id) if document else 0

    def _extract(self, parsed: NormalizedDocument) -> NormalizedDocument:
        """Block assembly.

        The parser already produced blocks; this stage is where we would add
        format-agnostic post-processing (for example merging a caption into the
        table it describes). Today it validates and normalises block ordering so
        downstream code can rely on `block_index` being contiguous and ascending.
        """
        ordered = sorted(parsed.blocks, key=lambda b: b.order)
        for index, block in enumerate(ordered):
            block.order = index
            block.metadata["block_index"] = index
        parsed.blocks = ordered
        return parsed

    def _embed_with_progress(self, texts: list[str]):
        """Embed in batches, updating the Document row between batches.

        This is what makes the Embedding stage move smoothly at a rate that
        reflects real work rather than an animation.
        """
        import numpy as np

        service = get_embedding_service()
        batch_size = max(1, settings.embedding_batch_size)
        total = len(texts)
        batches = (total + batch_size - 1) // batch_size

        collected: list[np.ndarray] = []
        for batch_index in range(batches):
            start = batch_index * batch_size
            end = min(start + batch_size, total)
            vectors = service.embed_documents(texts[start:end])
            collected.append(vectors)

            fraction = (batch_index + 1) / batches
            # Embedding occupies 0.58 -> 0.92 of the overall bar.
            progress = 0.58 + (0.92 - 0.58) * fraction
            with session_scope() as db:
                document = db.get(Document, self.document_id)
                if document is not None:
                    document.stage = "embedding"
                    document.progress = round(min(progress, 0.92), 4)
                    document.stage_timings = {
                        **self.timings,
                        "embedding_progress": f"{end}/{total}",
                    }
                    db.commit()

        if not collected:
            return np.zeros((0, service.dimension), dtype=np.float32)
        return np.vstack(collected)

    def _timed(self, stage: str, func: Callable[[], Any]) -> Any:
        """Run a stage, record its real duration, and store it for the trace."""
        start = time.perf_counter()
        result = func()
        duration_ms = int((time.perf_counter() - start) * 1000)
        self.stages.append(StageResult(stage=stage, duration_ms=duration_ms, detail={}))
        self.timings[stage] = duration_ms
        return result

    def _mark(self, db: Session, document: Document, stage: str, progress: float) -> None:
        """Persist a real stage transition."""
        document.stage = stage
        document.progress = progress
        document.status = "processing"
        db.commit()

    def _fail(self, message: str, code: str) -> None:
        """Record failure and remove any partial vectors."""
        try:
            with session_scope() as db:
                document = db.get(Document, self.document_id)
                if document is not None:
                    document.status = "failed"
                    document.stage = "failed"
                    document.error_message = message
                    document.stage_timings = self.timings
                    db.commit()
            # Clean up partial index writes so retrieval never sees half a document.
            try:
                get_vector_store().delete_document(self.document_id)
            except Exception:  # pragma: no cover
                logger.warning("Could not clean up vectors for failed document %s", self.document_id)
        except Exception:  # pragma: no cover
            logger.exception("Could not record ingestion failure for %s", self.document_id)
        logger.warning("Ingestion failed for document %s (%s): %s", self.document_id, code, message)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def run_ingestion(document_id: int) -> bool:
    """Synchronous ingestion. Used by tests and the CLI."""
    return IngestionPipeline(document_id).run()


def start_ingestion_async(document_id: int) -> threading.Thread:
    """Kick off ingestion on a background thread and return immediately.

    A daemon thread keeps the HTTP request fast (the upload returns instantly) and
    keeps the API responsive while a 200-page PDF is being processed.
    """
    thread = threading.Thread(
        target=run_ingestion,
        args=(document_id,),
        name=f"ingest-{document_id}",
        daemon=True,
    )
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Progress reporting for the UI (reads real DB state)
# ---------------------------------------------------------------------------
def document_progress(db: Session, document_id: int) -> dict[str, Any]:
    document = db.get(Document, document_id)
    if document is None:
        return {"found": False}

    stage = document.stage
    ordered = [name for name, _, _ in STAGES]
    index = ordered.index(stage) if stage in ordered else -1

    return {
        "found": True,
        "document_id": document.id,
        "status": document.status,
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage.title()),
        "stage_index": index,
        "stage_total": len(ordered),
        "progress": document.progress,
        "percent": round(document.progress * 100, 1),
        "error": document.error_message,
        "chunk_count": document.chunk_count,
        "timings": document.stage_timings or {},
        "stages": [
            {
                "name": name,
                "label": label,
                "state": (
                    "done"
                    if index > position or document.status == "ready"
                    else "active"
                    if index == position
                    else "pending"
                ),
                "duration_ms": (document.stage_timings or {}).get(name),
            }
            for position, (name, _, label) in enumerate(STAGES)
        ],
    }


def list_stage_definitions() -> list[dict[str, Any]]:
    """Exposed to the frontend so the UI and backend cannot drift apart."""
    return [
        {"name": name, "label": label, "target_progress": progress}
        for name, progress, label in STAGES
    ]


def reindex_document(document_id: int) -> bool:
    """Re-run embedding + indexing for an already-parsed document.

    Needed when the embedding model changes (spec section 9). The chunk text is
    still in SQLite, so we do not re-parse the original file.
    """
    with session_scope() as db:
        document = db.get(Document, document_id)
        if document is None:
            return False
        chunks = list(
            db.scalars(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
            )
        )
        if not chunks:
            # Nothing cached - fall back to a full re-run from the file.
            needs_full = True
        else:
            needs_full = False

    if needs_full:
        return run_ingestion(document_id)

    try:
        service = get_embedding_service()
        vectors = service.embed_documents([c.content for c in chunks])

        with session_scope() as db:
            document = db.get(Document, document_id)
            assert document is not None
            for chunk in chunks:
                chunk.embedding_model = settings.embedding_model
            document.embedding_model = settings.embedding_model
            document.embedding_dim = service.dimension
            db.commit()
            workspace_id = document.workspace_id

        store = get_vector_store()
        store.delete_document(document_id)
        store.add_chunks(
            workspace_id=workspace_id,
            document_id=document_id,
            vector_ids=[c.vector_id for c in chunks],
            contents=[c.content for c in chunks],
            embeddings=vectors,
            metadatas=[c.doc_metadata for c in chunks],
        )
        logger.info("Re-indexed document %s (%d chunks)", document_id, len(chunks))
        return True
    except Exception as exc:
        logger.exception("Re-index failed for document %s", document_id)
        _ = exc
        return False


def stale_documents(db: Session, workspace_id: int) -> list[dict[str, Any]]:
    """Documents whose vectors were built with a different embedding model."""
    current = settings.embedding_model
    rows = db.scalars(
        select(Document).where(
            Document.workspace_id == workspace_id,
            Document.status == "ready",
            Document.embedding_model != current,
        )
    )
    return [
        {
            "document_id": d.id,
            "document_name": d.original_filename,
            "embedded_with": d.embedding_model or "unknown",
            "current_model": current,
        }
        for d in rows
    ]
