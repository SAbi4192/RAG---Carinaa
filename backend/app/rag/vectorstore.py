"""
Vector store (ChromaDB).

WHAT A VECTOR DATABASE IS FOR
-----------------------------
Once every chunk is a vector, the question "which chunks are relevant to this
question?" becomes "which vectors are nearest to this vector?". Doing that by
brute force over 50,000 chunks is slow, so a vector database keeps an Approximate
Nearest Neighbour index (HNSW) and can answer in milliseconds.

It also stores a small amount of metadata alongside each vector, which is what
makes filtering possible.

WHY CHROMADB
------------
Spec section 10 asks for something simple, persistent, local, metadata-filterable
and easy to demonstrate. Chroma is embedded (no server to run), persists to a
folder, supports `where` filters, and you can inspect it from Python in one line
during the demo.

WHY WE STILL KEEP SQLITE
------------------------
Chroma is an *index*, not a source of truth. It stores the vector and a small
metadata payload. The full chunk text, the complete provenance, and every
document record live in SQLite. Consequences:
  * we can rebuild the entire vector index from SQLite (model change, corruption)
  * we can audit exactly what was retrieved, long after the fact
  * deleting a workspace is a SQL cascade plus one index delete

THE WORKSPACE FILTER IS NOT OPTIONAL
------------------------------------
Every query in this file goes through `_scope_filter`, which ALWAYS includes
`workspace_id`. There is no code path that searches across workspaces. On top of
that, `query()` re-verifies the workspace on every returned row before handing it
back (spec section 18, items 3 and 4). See tests/test_workspace_isolation.py.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from app.core.config import settings
from app.core.errors import VectorStoreError
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTION_NAME = "carinaa_chunks"

# Chroma only accepts str/int/float/bool metadata values.
_SCALAR_METADATA_KEYS: tuple[str, ...] = (
    "document_id",
    "document_name",
    "file_type",
    "chunk_index",
    "page_number",
    "page_end",
    "slide_number",
    "slide_end",
    "sheet_name",
    "section",
    "row_start",
    "row_end",
    "json_path",
    "block_type",
)


@dataclass(slots=True)
class RetrievedChunk:
    """One candidate returned by vector search."""

    vector_id: str
    workspace_id: int
    document_id: int
    chunk_index: int
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    # Cosine similarity in [-1, 1], computed from the distance Chroma returns.
    score: float = 0.0
    distance: float = 0.0
    rank: int = 0

    # Filled in later by the retriever once SQLite rows are joined.
    chunk_id: int | None = None
    document_name: str = ""
    file_type: str = ""

    # The score the vector search alone produced, before any lexical blending.
    # Kept so the trace can show what retrieval believed independently of the
    # adjustment - the blend is an amendment, never a replacement.
    original_score: float | None = None

    def citation_label(self) -> str:
        """Short human label, e.g. 'Cloud_Computing.pdf p.32'."""
        parts = [self.document_name or f"Document {self.document_id}"]
        page = self.metadata.get("page_number")
        slide = self.metadata.get("slide_number")
        sheet = self.metadata.get("sheet_name")
        if page:
            page_end = self.metadata.get("page_end")
            parts.append(
                f"p.{page}-{page_end}" if page_end and page_end != page else f"p.{page}"
            )
        elif slide:
            parts.append(f"slide {slide}")
        elif sheet:
            row_start = self.metadata.get("row_start")
            row_end = self.metadata.get("row_end")
            if row_start and row_end and row_end != row_start:
                parts.append(f"{sheet}, rows {row_start}-{row_end}")
            elif row_start:
                parts.append(f"{sheet}, row {row_start}")
            else:
                parts.append(str(sheet))
        return " · ".join(parts)


class VectorStore:
    """Thin, explicit wrapper over a persistent Chroma collection."""

    _instance: "VectorStore | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, path: Path | None = None, collection_name: str = COLLECTION_NAME) -> None:
        self.path = Path(path or settings.chroma_dir)
        self.path.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self._client: Any = None
        self._collection: Any = None
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- singleton
    @classmethod
    def instance(cls) -> "VectorStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    # -------------------------------------------------------------------- setup
    def _ensure_collection(self) -> Any:
        if self._collection is not None:
            return self._collection

        with self._lock:
            if self._collection is not None:
                return self._collection
            try:
                import chromadb
                from chromadb.config import Settings as ChromaSettings

                logger.info("Opening vector store at %s", self.path)
                self._client = chromadb.PersistentClient(
                    path=str(self.path),
                    settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
                )
                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name,
                    # Cosine distance matches our L2-normalised embeddings.
                    metadata={"hnsw:space": "cosine"},
                )
                return self._collection
            except Exception as exc:
                logger.error("Vector store unavailable: %s", exc)
                raise VectorStoreError(
                    "The local vector store could not be opened.",
                    detail={"reason": exc.__class__.__name__},
                ) from exc

    # ------------------------------------------------------------------- writes
    def add_chunks(
        self,
        *,
        workspace_id: int,
        document_id: int,
        vector_ids: Sequence[str],
        contents: Sequence[str],
        embeddings: np.ndarray,
        metadatas: Sequence[dict[str, Any]],
    ) -> int:
        """Insert (or replace) vectors for one document.

        `workspace_id` is written into every row's metadata. That single field is
        what makes workspace isolation enforceable at query time rather than merely
        intended.
        """
        if not vector_ids:
            return 0

        # NOTE: this must NOT be written as `len(a) != len(b) != len(c) != len(d)`.
        # Python chains comparisons, so that expression actually means
        # `(a != b) and (b != c) and (c != d)` - which is False, and therefore
        # raises nothing, whenever the first two lengths happen to match. A payload
        # of lengths [1, 1, 1, 2] would slip through and fail later inside Chroma
        # with a far less useful message. Compare against one expected length.
        expected = len(vector_ids)
        if not (
            len(contents) == expected
            and len(metadatas) == expected
            and len(embeddings) == expected
        ):
            raise VectorStoreError(
                "Vector store payload lengths do not match.",
                detail={
                    "vector_ids": expected,
                    "contents": len(contents),
                    "metadatas": len(metadatas),
                    "embeddings": len(embeddings),
                },
            )

        collection = self._ensure_collection()

        payload_metadatas = [
            _to_chroma_metadata(meta, workspace_id=workspace_id, document_id=document_id)
            for meta in metadatas
        ]

        try:
            with self._lock:
                collection.upsert(
                    ids=list(vector_ids),
                    embeddings=[v.tolist() for v in embeddings],
                    documents=list(contents),
                    metadatas=payload_metadatas,
                )
            logger.info(
                "Indexed %d vectors (workspace=%s document=%s)",
                len(vector_ids),
                workspace_id,
                document_id,
            )
            return len(vector_ids)
        except Exception as exc:
            logger.error("Vector upsert failed: %s", exc)
            raise VectorStoreError(
                "Could not write vectors to the index.",
                detail={"reason": exc.__class__.__name__},
            ) from exc

    def delete_document(self, document_id: int) -> None:
        """Remove every vector belonging to a document."""
        collection = self._ensure_collection()
        try:
            with self._lock:
                collection.delete(where={"document_id": int(document_id)})
            logger.info("Removed vectors for document %s", document_id)
        except Exception as exc:  # pragma: no cover
            logger.warning("Could not delete vectors for document %s: %s", document_id, exc)

    def delete_workspace(self, workspace_id: int) -> None:
        collection = self._ensure_collection()
        try:
            with self._lock:
                collection.delete(where={"workspace_id": int(workspace_id)})
            logger.info("Removed vectors for workspace %s", workspace_id)
        except Exception as exc:  # pragma: no cover
            logger.warning("Could not delete vectors for workspace %s: %s", workspace_id, exc)

    # ------------------------------------------------------------------ reads
    def query(
        self,
        *,
        workspace_id: int,
        query_embedding: np.ndarray,
        top_k: int = 5,
        document_ids: Sequence[int] | None = None,
        page_number: int | None = None,
        section: str | None = None,
    ) -> list[RetrievedChunk]:
        """Nearest-neighbour search, hard-scoped to one workspace.

        Two independent protections against cross-workspace leakage:
          1. the `where` filter is applied inside the index (so foreign vectors are
             never even scored)
          2. every returned row is re-checked against `workspace_id` below, so a
             bug or a Chroma behaviour change cannot silently leak data
        """
        if top_k <= 0:
            return []

        collection = self._ensure_collection()
        where = self._scope_filter(workspace_id, document_ids, page_number, section)

        try:
            with self._lock:
                result = collection.query(
                    query_embeddings=[np.asarray(query_embedding, dtype=np.float32).tolist()],
                    n_results=top_k,
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
        except Exception as exc:
            logger.error("Vector query failed: %s", exc)
            raise VectorStoreError(
                "The vector search could not be completed.",
                detail={"reason": exc.__class__.__name__},
            ) from exc

        return self._to_retrieved(result, expected_workspace_id=workspace_id)

    def _scope_filter(
        self,
        workspace_id: int,
        document_ids: Sequence[int] | None,
        page_number: int | None = None,
        section: str | None = None,
    ) -> dict[str, Any]:
        """Build the Chroma `where` clause.

        `workspace_id` is unconditional. `document_ids` and `page_number` only ever
        narrow WITHIN an already-scoped workspace, so neither can be used to escape
        the boundary.

        Page matching accepts a chunk that STARTS on the page or ENDS on it. Chunks
        can span a page boundary - a passage beginning on page 2 and finishing on
        page 3 is legitimately part of both - and matching only `page_number` would
        hide it from a question about page 3.
        """
        conditions: list[dict[str, Any]] = [{"workspace_id": int(workspace_id)}]

        if document_ids:
            ids = [int(d) for d in document_ids]
            if len(ids) == 1:
                conditions.append({"document_id": ids[0]})
            else:
                conditions.append({"document_id": {"$in": ids}})

        if page_number is not None and page_number > 0:
            target = int(page_number)
            # A chunk COVERS a page when it starts at or before it and ends at or
            # after it. Matching equality on either bound misses a chunk that spans
            # the page - and a short document is one chunk spanning all of its pages,
            # so this is the common case rather than an edge case.
            conditions.append(
                {
                    "$and": [
                        {"page_number": {"$lte": target}},
                        {"page_end": {"$gte": target}},
                    ]
                }
            )

        if section:
            # Exact match on the stored title. The caller matches the question against
            # the real section list first and passes back the exact string, so this
            # never has to guess at a fuzzy comparison the index cannot do.
            conditions.append({"section": section})

        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def _to_retrieved(
        self, result: dict[str, Any], *, expected_workspace_id: int
    ) -> list[RetrievedChunk]:
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        out: list[RetrievedChunk] = []
        rejected = 0

        for rank, vector_id in enumerate(ids):
            metadata = dict(metadatas[rank] or {}) if rank < len(metadatas) else {}
            distance = float(distances[rank]) if rank < len(distances) else 1.0
            content = documents[rank] if rank < len(documents) else ""

            row_workspace = int(metadata.get("workspace_id", -1))

            # ---- post-retrieval verification (defence in depth) -------------
            if row_workspace != int(expected_workspace_id):
                rejected += 1
                logger.error(
                    "BLOCKED cross-workspace vector: expected workspace %s, got %s "
                    "(vector_id=%s). This should be impossible - please investigate.",
                    expected_workspace_id,
                    row_workspace,
                    vector_id,
                )
                continue

            out.append(
                RetrievedChunk(
                    vector_id=str(vector_id),
                    workspace_id=row_workspace,
                    document_id=int(metadata.get("document_id", 0)),
                    chunk_index=int(metadata.get("chunk_index", 0)),
                    content=content or "",
                    metadata=metadata,
                    # Chroma cosine distance -> similarity. Clamped so a tiny
                    # floating-point overshoot never shows as 1.0000000002 in the UI.
                    score=max(-1.0, min(1.0, 1.0 - distance)),
                    distance=distance,
                    rank=rank,
                    document_name=str(metadata.get("document_name", "")),
                    file_type=str(metadata.get("file_type", "")),
                )
            )

        if rejected:
            logger.error("Rejected %d cross-workspace vector(s) during verification.", rejected)
        return out

    # ------------------------------------------------------------------- stats
    def count(self, workspace_id: int | None = None) -> int:
        collection = self._ensure_collection()
        try:
            if workspace_id is None:
                return int(collection.count())
            result = collection.get(where={"workspace_id": int(workspace_id)}, include=[])
            return len(result.get("ids") or [])
        except Exception as exc:  # pragma: no cover
            logger.warning("Vector count failed: %s", exc)
            return 0

    def document_vector_count(self, document_id: int) -> int:
        collection = self._ensure_collection()
        try:
            result = collection.get(where={"document_id": int(document_id)}, include=[])
            return len(result.get("ids") or [])
        except Exception as exc:  # pragma: no cover
            logger.warning("Vector count for document failed: %s", exc)
            return 0

    def health(self) -> dict[str, Any]:
        try:
            collection = self._ensure_collection()
            return {
                "ok": True,
                "backend": "chromadb",
                "path": str(self.path),
                "collection": self.collection_name,
                "vectors": int(collection.count()),
                "persistent": True,
                "embedded": True,
            }
        except Exception as exc:
            return {"ok": False, "backend": "chromadb", "error": exc.__class__.__name__}


# ---------------------------------------------------------------------------
# Metadata flattening
# ---------------------------------------------------------------------------
def _to_chroma_metadata(
    metadata: dict[str, Any], *, workspace_id: int, document_id: int
) -> dict[str, Any]:
    """Flatten chunk metadata into Chroma's scalar-only format.

    Chroma rejects None, nested dicts and lists. We keep the scalar provenance
    keys the UI needs for citations and drop the rest - the authoritative copy is
    still in SQLite, so nothing is lost.
    """
    out: dict[str, Any] = {
        "workspace_id": int(workspace_id),
        "document_id": int(document_id),
    }

    for key in _SCALAR_METADATA_KEYS:
        value = metadata.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (int, float)):
            out[key] = value
        elif isinstance(value, str):
            out[key] = value[:500]
        else:
            continue

    # Chroma requires at least one non-empty metadata field per row; the two we
    # always set above guarantee that.
    return out


def get_vector_store() -> VectorStore:
    return VectorStore.instance()
