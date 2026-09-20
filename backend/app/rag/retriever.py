"""
Retrieval.

WHAT RETRIEVAL IS
-----------------
Retrieval answers one question: *given this question, which pieces of my documents
are worth showing the language model?*

The mechanism is dense vector search:

    question -> embed -> a vector
                          |
                          v
    compare against every stored chunk vector (cosine similarity)
                          |
                          v
    take the K nearest  ->  those are the candidates

WHY COSINE SIMILARITY
---------------------
Every vector is L2-normalised, so cosine similarity is just a dot product, and it
ranges from -1 (opposite) through 0 (unrelated) to 1 (identical meaning). Chroma
returns a *distance* (1 - similarity) because it indexes for nearest-neighbour
search; `VectorStore` converts it back so everything the user sees is a similarity
where higher is better.

THE TWO-STAGE SHAPE (spec section 11 and 12)
--------------------------------------------
    candidate_k = 20   retrieved from the vector index     (optimise recall)
    top_k       = 5    handed to the language model        (optimise precision)

Retrieving 20 and using 5 costs almost nothing extra - the expensive part is the
LLM call, not the vector lookup - and it gives the re-ranker something to work with
if it is enabled.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
No query rewriting, no multi-query expansion, no hybrid BM25 fusion. Those are
Phase 7 experiments (spec section 12) and each one adds a moving part the team
would have to defend. Basic RAG has to be excellent before advanced RAG is worth
attempting.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NoWorkspaceDocuments, RetrievalError
from app.core.logging import get_logger
from app.db.models import Chunk
from app.rag.embeddings import get_embedding_service
from app.rag.reranker import get_reranker
from app.rag.trace import TraceRecorder
from app.rag.vectorstore import RetrievedChunk, get_vector_store

logger = get_logger(__name__)

_WHITESPACE = re.compile(r"\s+")


@dataclass
class QueryAnalysis:
    """What we understood about the question before searching.

    Kept deliberately simple and fully explainable: normalisation, a language hint,
    and a shape classification. There is no hidden model call here.
    """

    original: str
    normalized: str
    language: str
    is_question: bool
    word_count: int
    keywords: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "normalized": self.normalized,
            "language": self.language,
            "is_question": self.is_question,
            "word_count": self.word_count,
            "keywords": self.keywords[:12],
            "note": (
                "The question is normalised and embedded as-is. No query rewriting is "
                "applied at this stage."
            ),
        }


@dataclass
class RetrievalOutcome:
    """Everything retrieval produced, with real measurements."""

    chunks: list[RetrievedChunk] = field(default_factory=list)
    analysis: QueryAnalysis | None = None

    candidates_retrieved: int = 0
    duplicates_removed: int = 0
    below_threshold: int = 0
    cross_workspace_rejected: int = 0

    embedding_ms: int = 0
    search_ms: int = 0
    rerank_ms: int = 0
    total_ms: int = 0

    reranked: bool = False
    rerank_info: dict[str, Any] = field(default_factory=dict)

    @property
    def top_score(self) -> float:
        return max((c.score for c in self.chunks), default=0.0)

    @property
    def mean_score(self) -> float:
        if not self.chunks:
            return 0.0
        return sum(c.score for c in self.chunks) / len(self.chunks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidates_retrieved": self.candidates_retrieved,
            "returned": len(self.chunks),
            "duplicates_removed": self.duplicates_removed,
            "below_threshold": self.below_threshold,
            "top_score": round(self.top_score, 4),
            "mean_score": round(self.mean_score, 4),
            "embedding_ms": self.embedding_ms,
            "search_ms": self.search_ms,
            "rerank_ms": self.rerank_ms,
            "total_ms": self.total_ms,
            "reranked": self.reranked,
            "rerank": self.rerank_info,
            "analysis": self.analysis.as_dict() if self.analysis else None,
            "chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "document_id": c.document_id,
                    "document_name": c.document_name,
                    "file_type": c.file_type,
                    "chunk_index": c.chunk_index,
                    "score": round(c.score, 4),
                    "rank": c.rank,
                    "original_rank": getattr(c, "original_rank", None),
                    "rerank_score": getattr(c, "rerank_score", None),
                    "label": c.citation_label(),
                    "metadata": c.metadata,
                    "content": c.content,
                    "preview": c.content[:280] + ("..." if len(c.content) > 280 else ""),
                }
                for c in self.chunks
            ],
        }


# ---------------------------------------------------------------------------
# Query analysis
# ---------------------------------------------------------------------------
_QUESTION_WORDS = (
    "what", "why", "how", "when", "where", "which", "who", "whose", "whom",
    "is", "are", "was", "were", "does", "do", "did", "can", "could", "should",
    "would", "will", "explain", "describe", "summarise", "summarize", "list",
    "compare", "define", "tell",
)


def analyze_query(question: str) -> QueryAnalysis:
    """Normalise and classify the question. No model call, no network."""
    from app.ingestion.types import detect_language_hint

    original = question
    normalized = _WHITESPACE.sub(" ", question).strip()

    words = [w for w in re.findall(r"[\w'\-]+", normalized.lower()) if len(w) > 2]
    is_question = normalized.endswith("?") or (
        bool(words) and words[0] in _QUESTION_WORDS
    )

    # Crude keyword extraction: content words, longest first, de-duplicated. Used
    # only for display and for the lexical half of the grounding check.
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "what", "why", "how",
        "when", "where", "which", "who", "are", "was", "were", "does", "did",
        "can", "could", "should", "would", "will", "explain", "describe", "about",
        "into", "than", "then", "them", "they", "there", "here", "have", "has",
    }
    keywords: list[str] = []
    for word in sorted(set(words), key=len, reverse=True):
        if word not in stop and word not in keywords:
            keywords.append(word)
        if len(keywords) >= 12:
            break

    return QueryAnalysis(
        original=original,
        normalized=normalized,
        language=detect_language_hint(normalized),
        is_question=is_question,
        word_count=len(normalized.split()),
        keywords=keywords,
    )


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------
class Retriever:
    """Dense retrieval over one workspace."""

    def __init__(self) -> None:
        self.embeddings = get_embedding_service()
        self.store = get_vector_store()

    def retrieve(
        self,
        db: Session,
        *,
        workspace_id: int,
        question: str,
        top_k: int | None = None,
        candidate_k: int | None = None,
        document_ids: Sequence[int] | None = None,
        use_rerank: bool | None = None,
        trace: TraceRecorder | None = None,
    ) -> RetrievalOutcome:
        """Full retrieval: analyse, embed, search, optionally re-rank, join."""
        started = time.perf_counter()
        outcome = RetrievalOutcome()

        top_k = top_k or settings.top_k
        candidate_k = max(candidate_k or settings.candidate_k, top_k)
        rerank_enabled = settings.rerank_enabled if use_rerank is None else use_rerank

        # ---------------------------------------------------------------
        # Stage 1 - query analysis
        # ---------------------------------------------------------------
        if trace:
            with trace.stage("query_analysis") as info:
                outcome.analysis = analyze_query(question)
                info.update(
                    {
                        "language": outcome.analysis.language,
                        "word_count": outcome.analysis.word_count,
                        "is_question": outcome.analysis.is_question,
                        "keywords": outcome.analysis.keywords[:8],
                    }
                )
        else:
            outcome.analysis = analyze_query(question)

        # ---------------------------------------------------------------
        # Stage 2 - query embedding (same model as the chunks)
        # ---------------------------------------------------------------
        embed_start = time.perf_counter()
        if trace:
            with trace.stage("query_embedding") as info:
                query_vector = self.embeddings.embed_query(outcome.analysis.normalized)
                info["model"] = self.embeddings.model_name
                info["dimension"] = int(query_vector.shape[-1])
        else:
            query_vector = self.embeddings.embed_query(outcome.analysis.normalized)
        outcome.embedding_ms = int((time.perf_counter() - embed_start) * 1000)

        # ---------------------------------------------------------------
        # Stage 3 - vector search (workspace-scoped, always)
        # ---------------------------------------------------------------
        search_start = time.perf_counter()
        try:
            candidates = self.store.query(
                workspace_id=workspace_id,
                query_embedding=query_vector,
                top_k=candidate_k,
                document_ids=document_ids,
            )
        except Exception as exc:
            logger.exception("Retrieval failed for workspace %s", workspace_id)
            raise RetrievalError(
                "The vector search could not be completed.",
                detail={"reason": exc.__class__.__name__},
            ) from exc
        outcome.search_ms = int((time.perf_counter() - search_start) * 1000)
        outcome.candidates_retrieved = len(candidates)

        if trace:
            trace.add(
                "vector_search",
                status="ok" if candidates else "ok",
                duration_ms=outcome.search_ms,
                data={
                    "workspace_id": workspace_id,
                    "top_k_requested": candidate_k,
                    "candidates_returned": len(candidates),
                    "distance_metric": "cosine",
                    "document_filter": list(document_ids) if document_ids else None,
                    "workspace_filter_applied": True,
                },
            )

        if not candidates:
            if trace:
                trace.skip(
                    "candidate_retrieval",
                    "the vector index returned no candidates for this workspace",
                )
                trace.skip("reranking", "there were no candidates to re-rank")
            outcome.total_ms = int((time.perf_counter() - started) * 1000)
            self._assert_workspace_has_documents(db, workspace_id)
            return outcome

        # ---------------------------------------------------------------
        # Stage 4 - join SQLite, dedupe, threshold
        # ---------------------------------------------------------------
        candidates = self._join_chunk_rows(db, candidates, workspace_id)
        candidates, duplicates = self._dedupe(candidates)
        outcome.duplicates_removed = duplicates

        threshold = settings.min_relevance_score
        if threshold > 0:
            before = len(candidates)
            candidates = [c for c in candidates if c.score >= threshold]
            outcome.below_threshold = before - len(candidates)

        if trace:
            trace.add(
                "candidate_retrieval",
                duration_ms=0,
                data={
                    "after_dedup": len(candidates),
                    "duplicates_removed": duplicates,
                    "below_threshold_removed": outcome.below_threshold,
                    "score_threshold": threshold,
                    "near_duplicate_threshold": settings.retrieval_near_duplicate_threshold,
                    "top_score": round(candidates[0].score, 4) if candidates else 0.0,
                    "bottom_score": round(candidates[-1].score, 4) if candidates else 0.0,
                },
            )

        # ---------------------------------------------------------------
        # Stage 5 - optional re-ranking
        # ---------------------------------------------------------------
        if rerank_enabled and candidates:
            reranker = get_reranker()
            result = reranker.rerank(
                outcome.analysis.normalized, candidates, top_n=top_k
            )
            outcome.rerank_ms = result.get("duration_ms", 0)
            outcome.reranked = bool(result.get("applied"))
            outcome.rerank_info = {
                k: v for k, v in result.items() if k not in ("chunks", "comparison")
            }
            if result.get("applied"):
                candidates = result["chunks"]
                outcome.rerank_info["comparison"] = result.get("comparison", [])
            if trace:
                if result.get("applied"):
                    trace.add(
                        "reranking",
                        duration_ms=outcome.rerank_ms,
                        data={
                            "model": result.get("model"),
                            "candidates": result.get("candidates"),
                            "kept": result.get("kept"),
                            "positions_changed": result.get("moved"),
                            "note": (
                                "Re-ranking reorders existing candidates. It cannot "
                                "introduce evidence that retrieval did not find."
                            ),
                        },
                    )
                else:
                    trace.skip(
                        "reranking",
                        result.get("reason", "re-ranker unavailable"),
                    )
        else:
            if trace:
                trace.skip(
                    "reranking",
                    "disabled in settings (it is optional and not required for basic RAG)",
                )
            outcome.rerank_info = {
                "enabled": False,
                "note": "Re-ranking is disabled. Candidates are ordered by cosine similarity.",
            }

        outcome.chunks = candidates[:top_k]
        outcome.total_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    # ------------------------------------------------------------------ helpers
    def _join_chunk_rows(
        self, db: Session, candidates: list[RetrievedChunk], workspace_id: int
    ) -> list[RetrievedChunk]:
        """Attach SQLite chunk ids and re-verify workspace ownership.

        The vector store is an index, not the source of truth. This join is where
        the authoritative record is confirmed - and where a second, independent
        workspace check happens. If a chunk's `workspace_id` in SQLite disagrees
        with the requested workspace, it is dropped and logged loudly.
        """
        vector_ids = [c.vector_id for c in candidates if c.vector_id]
        if not vector_ids:
            return candidates

        rows = db.scalars(
            select(Chunk).where(
                Chunk.vector_id.in_(vector_ids),
                Chunk.workspace_id == workspace_id,  # hard filter, again
            )
        ).all()
        by_vector_id = {row.vector_id: row for row in rows}

        joined: list[RetrievedChunk] = []
        for candidate in candidates:
            row = by_vector_id.get(candidate.vector_id)
            if row is None:
                logger.error(
                    "Vector %s has no matching chunk in workspace %s - dropping it.",
                    candidate.vector_id,
                    workspace_id,
                )
                continue
            if int(row.workspace_id) != int(workspace_id):
                logger.error(
                    "BLOCKED cross-workspace chunk at join: chunk %s belongs to "
                    "workspace %s, requested %s.",
                    row.id,
                    row.workspace_id,
                    workspace_id,
                )
                continue

            candidate.chunk_id = int(row.id)
            candidate.chunk_index = int(row.chunk_index)
            candidate.document_id = int(row.document_id)
            candidate.content = row.content
            # Prefer the SQLite copy of the metadata: it is complete, whereas the
            # vector store only keeps scalar fields.
            merged = {**candidate.metadata, **(row.doc_metadata or {})}
            candidate.metadata = merged
            candidate.document_name = str(
                merged.get("document_name") or candidate.document_name or ""
            )
            candidate.file_type = str(merged.get("file_type") or candidate.file_type or "")
            joined.append(candidate)

        return joined

    def _dedupe(self, candidates: list[RetrievedChunk]) -> tuple[list[RetrievedChunk], int]:
        """Drop duplicate chunks, keeping the highest-scoring copy of each.

        Two passes, because exact matching alone is not enough.

        PASS 1 — identical text.
            Catches a chunk that was indexed twice.

        PASS 2 — near-duplicate content across formats.
            This is the case that actually breaks things. The same material often
            exists as both a PDF and an exported Markdown or Word file, and a
            converter changes hyphenation, whitespace, soft breaks and header text.
            The strings differ, so exact matching sails straight past them.

            The consequences are worse than a little redundancy: the near-duplicates
            compete for the same `top_k` slots, so the model receives the same
            evidence two or three times under different filenames while genuinely
            different evidence is pushed out of the context. The source list then
            shows "ADT_Notes.pdf" and "ADT_Notes.md" as if they were two independent
            sources supporting the answer, which is not what happened.

        Similarity is Jaccard over word sets: robust to the exact differences
        converters introduce, and cheap enough to run over the candidate pool.
        """
        # ---- pass 1: identical normalised text ------------------------
        seen: dict[str, RetrievedChunk] = {}
        removed = 0
        for candidate in candidates:
            fingerprint = " ".join(candidate.content.lower().split())[:400]
            existing = seen.get(fingerprint)
            if existing is None:
                seen[fingerprint] = candidate
            else:
                removed += 1
                if candidate.score > existing.score:
                    seen[fingerprint] = candidate

        survivors = sorted(seen.values(), key=lambda c: c.score, reverse=True)
        if len(survivors) < 2:
            return survivors, removed

        # ---- pass 2: near-duplicate content ---------------------------
        threshold = settings.retrieval_near_duplicate_threshold
        if threshold <= 0 or threshold >= 1:
            return survivors, removed

        kept: list[RetrievedChunk] = []
        kept_sets: list[frozenset[str]] = []

        for candidate in survivors:
            words = _content_word_set(candidate.content)
            if not words:
                kept.append(candidate)
                kept_sets.append(words)
                continue

            duplicate_of = None
            for index, existing_words in enumerate(kept_sets):
                if not existing_words:
                    continue
                union = len(words | existing_words)
                if union == 0:
                    continue

                shared = len(words & existing_words)
                jaccard = shared / union

                # Jaccard alone penalises SHORT chunks. Two extra header tokens on a
                # nine-word passage move it from 0.85 to 0.82, so a real duplicate
                # slips through purely because the passage is brief. The overlap
                # coefficient (shared / smaller set) is length-aware: if nearly all of
                # the shorter chunk's words appear in the other, they are the same
                # evidence regardless of how much boilerplate wraps the longer one.
                smaller = min(len(words), len(existing_words))
                containment = shared / smaller if smaller else 0.0

                if jaccard >= threshold or (
                    containment >= _MIN_CONTAINMENT and shared >= _MIN_SHARED_WORDS
                ):
                    duplicate_of = index
                    break

            if duplicate_of is None:
                kept.append(candidate)
                kept_sets.append(words)
            else:
                removed += 1
                # Keep whichever copy matched the question better.
                if candidate.score > kept[duplicate_of].score:
                    kept[duplicate_of] = candidate
                    kept_sets[duplicate_of] = words

        kept.sort(key=lambda c: c.score, reverse=True)
        return kept, removed


# Guards for the containment rule. `shared >= 6` stops two chunks that merely share
# a few common words from being collapsed; `containment >= 0.92` means the shorter
# chunk is essentially a copy of, or a fragment of, the longer one.
_MIN_CONTAINMENT = 0.92
_MIN_SHARED_WORDS = 6


def _content_word_set(text: str) -> frozenset[str]:
    """Words lowercased and stripped to alphanumerics, so `user-persona` and
    `user persona` collapse to the same token."""
    return frozenset(re.findall(r"[a-z0-9]+", (text or "").lower()))

    def _assert_workspace_has_documents(self, db: Session, workspace_id: int) -> None:
        """Distinguish 'nothing matched' from 'there is nothing to match against'.

        These are very different for the user: one means "rephrase your question",
        the other means "upload a document first".
        """
        count = db.scalar(
            select(Chunk.id).where(Chunk.workspace_id == workspace_id).limit(1)
        )
        if count is None:
            raise NoWorkspaceDocuments(
                "This workspace has no indexed documents yet. Upload a document first, "
                "then ask your question."
            )


def get_retriever() -> Retriever:
    return Retriever()
