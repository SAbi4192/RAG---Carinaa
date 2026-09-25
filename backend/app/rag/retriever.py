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
# units has no dependency on the retriever, so this cannot create a cycle.
from app.rag.units import detect_unit_reference
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

    # Which retriever actually produced this ranking, and the score scale the
    # `score` fields are on. Reported alongside the numbers so a UI can never
    # label a reciprocal-rank score as a cosine similarity.
    mode: str = "dense"
    score_scale: str = "cosine"
    # Full three-list comparison (dense, bm25, fused) when hybrid ran.
    hybrid: dict[str, Any] = field(default_factory=dict)

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
            # Which retriever produced this ranking and on what scale, so no UI
            # can mislabel an RRF or BM25 number as a cosine similarity.
            "mode": self.mode,
            "score_scale": self.score_scale,
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
            # The three rankings (dense / bm25 / fused) when hybrid ran, so the
            # Retrieval Lab and Learning Mode can show what fusion actually did
            # instead of only its result. Empty otherwise.
            "hybrid": self.hybrid,
            "analysis": self.analysis.as_dict() if self.analysis else None,
            "chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "document_id": c.document_id,
                    "document_name": c.document_name,
                    "file_type": c.file_type,
                    "chunk_index": c.chunk_index,
                    "vector_id": c.vector_id,
                    "score": round(c.score, 4),
                    "rank": c.rank,
                    "original_rank": getattr(c, "original_rank", None),
                    "rerank_score": getattr(c, "rerank_score", None),
                    # Hybrid provenance: where each method ranked this chunk, so a
                    # fused answer can show it moved (or did not) without hiding the
                    # source. None when the mode did not compute it.
                    "original_score": c.original_score,
                    "bm25_score": getattr(c, "bm25_score", None),
                    "bm25_rank": getattr(c, "bm25_rank", None),
                    "rrf_score": getattr(c, "rrf_score", None),
                    "rrf_rank": getattr(c, "rrf_rank", None),
                    "label": c.citation_label(),
                    "metadata": c.metadata,
                    "content": c.content,
                    "char_start": getattr(c, "char_start", None),
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
        page_number: int | None = None,
        page_span: tuple[int, int] | None = None,
        section: str | None = None,
        use_rerank: bool | None = None,
        trace: TraceRecorder | None = None,
        mode: str | None = None,
    ) -> RetrievalOutcome:
        """Full retrieval: analyse, search (dense/bm25/hybrid), join, optionally re-rank."""
        started = time.perf_counter()
        outcome = RetrievalOutcome()

        top_k = top_k or settings.top_k
        candidate_k = max(candidate_k or settings.candidate_k, top_k)
        rerank_enabled = settings.rerank_enabled if use_rerank is None else use_rerank

        # One retriever mode for the run, resolved once. `dense` is the default
        # and the ONLY mode this codebase had before hybrid retrieval existed, so
        # a run that does not ask for a mode behaves exactly as it did then -
        # the same vectors, the same blend, the same ranking.
        mode = (mode or settings.retrieval_mode or "dense").strip().lower()
        if mode not in ("dense", "bm25", "hybrid"):
            mode = "dense"
        outcome.mode = mode
        outcome.score_scale = {"dense": "cosine", "bm25": "bm25", "hybrid": "rrf"}[mode]

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
                        "retrieval_mode": mode,
                    }
                )
        else:
            outcome.analysis = analyze_query(question)

        # ---------------------------------------------------------------
        # Stage 2 - query embedding. Needed by the vector search, so dense
        # and hybrid do it; BM25-only does not, and an embedding that is
        # never used should not be billed to the user as if it were.
        # ---------------------------------------------------------------
        query_vector = None
        if mode in ("dense", "hybrid"):
            embed_start = time.perf_counter()
            if trace:
                with trace.stage("query_embedding") as info:
                    query_vector = self.embeddings.embed_query(outcome.analysis.normalized)
                    info["model"] = self.embeddings.model_name
                    info["dimension"] = int(query_vector.shape[-1])
            else:
                query_vector = self.embeddings.embed_query(outcome.analysis.normalized)
            outcome.embedding_ms = int((time.perf_counter() - embed_start) * 1000)
        elif trace:
            trace.skip(
                "query_embedding",
                "keyword-only (BM25) retrieval compares words, not vectors, "
                "so no embedding is computed",
            )

        # ---------------------------------------------------------------
        # Stage 3 - search. Which search depends on the mode:
        #   dense   the vector index only
        #   bm25    the lexical index only
        #   hybrid  both, fused by reciprocal rank
        # All three see the SAME population: the same workspace, the same
        # document/page/section scope. Otherwise the Retrieval Lab would be
        # comparing methods over different corpora, which is not a comparison.
        # ---------------------------------------------------------------
        search_start = time.perf_counter()
        fused_hybrid: dict[str, Any] = {}
        candidates: list[RetrievedChunk] = []

        if mode in ("dense", "hybrid"):
            # Began BEFORE the network call so the live pipeline can mark the
            # stage running while the search is still in flight; `add()` below
            # closes the same event instead of appending a second one.
            search_event = trace.begin("vector_search") if trace else None
            try:
                try:
                    candidates = self.store.query(
                        workspace_id=workspace_id,
                        query_embedding=query_vector,
                        top_k=candidate_k,
                        document_ids=document_ids,
                        page_number=page_number,
                        page_span=page_span,
                        section=section,
                    )
                except RetrievalError:
                    raise
                except Exception as exc:
                    logger.exception("Retrieval failed for workspace %s", workspace_id)
                    raise RetrievalError(
                        "The vector search could not be completed.",
                        detail={"reason": exc.__class__.__name__},
                    ) from exc
            except RetrievalError:
                # The live step must not stay "running" after a failed search;
                # close it as an error before the exception reaches the caller.
                if trace and search_event is not None:
                    trace.add(
                        "vector_search",
                        status="error",
                        duration_ms=int((time.perf_counter() - search_start) * 1000),
                    )
                raise
            outcome.search_ms = int((time.perf_counter() - search_start) * 1000)

            if trace:
                trace.add(
                    "vector_search",
                    status="ok",
                    duration_ms=outcome.search_ms,
                    data={
                        "workspace_id": workspace_id,
                        "top_k_requested": candidate_k,
                        "candidates_returned": len(candidates),
                        "distance_metric": "cosine",
                        "document_filter": list(document_ids) if document_ids else None,
                        "page_filter": page_number,
                        "page_span_filter": list(page_span) if page_span else None,
                        "section_filter": section,
                        "workspace_filter_applied": True,
                    },
                )
        elif trace:
            trace.skip(
                "vector_search",
                "keyword-only (BM25) retrieval ranks by exact terms, "
                "so the vector index is not queried",
            )

        if mode in ("bm25", "hybrid"):
            candidates, fused_hybrid, bm25_ms = self._lexical_search(
                db,
                workspace_id=workspace_id,
                question=question,
                candidates=candidates,
                mode=mode,
                top_k=top_k,
                candidate_k=candidate_k,
                document_ids=document_ids,
                page_number=page_number,
                page_span=page_span,
                section=section,
                trace=trace,
            )
            outcome.search_ms += bm25_ms

        outcome.candidates_retrieved = len(candidates)
        outcome.hybrid = fused_hybrid

        if not candidates:
            if trace:
                trace.skip(
                    "candidate_retrieval",
                    "retrieval returned no candidates for this workspace and scope",
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
        # The cosine threshold and the lexical blend are DENSE-specific.
        # Under bm25 the scores are BM25 numbers, and under hybrid they are
        # reciprocal-rank values (~0.005-0.03) - filtering either scale at a
        # cosine cutoff would discard everything (or nothing), and blending a
        # lexical score into BM25 would double-count the exact-term evidence the
        # ranking already contains. Both scales are honest; they are just not
        # comparable, so no cross-scale cutoff is applied.
        blend_applied = mode == "dense"
        if threshold > 0 and mode == "dense":
            before = len(candidates)
            candidates = [c for c in candidates if c.score >= threshold]
            outcome.below_threshold = before - len(candidates)

        if mode == "dense":
            # Blend in a lexical term BEFORE the final cut, so a chunk that literally
            # contains "UNIT III" is not discarded for a weak semantic score.
            #
            # Called ONCE. It was previously called twice in a row, which re-scored every
            # candidate against the same question a second time - the blend is not
            # idempotent, so the second pass silently shifted every score again and the
            # ranking shown in the trace was not the ranking the first pass produced.
            candidates = apply_lexical_blend(candidates, question)
        elif mode == "bm25":
            # `_lexical_search` already put the BM25 score in `score`; keep that order.
            candidates.sort(key=lambda c: c.score, reverse=True)
            for position, chunk in enumerate(candidates):
                chunk.rank = position
        else:  # hybrid: the order is already decided by the reciprocal-rank fusion
            candidates.sort(key=lambda c: c.rrf_score or 0.0, reverse=True)
            for position, chunk in enumerate(candidates):
                chunk.rank = position

        if trace:
            trace.add(
                "candidate_retrieval",
                duration_ms=0,
                data={
                    "retrieval_mode": mode,
                    "score_scale": outcome.score_scale,
                    "after_dedup": len(candidates),
                    "duplicates_removed": duplicates,
                    "below_threshold_removed": outcome.below_threshold,
                    "score_threshold": threshold if mode != "hybrid" else None,
                    "lexical_blend": blend_applied,
                    "near_duplicate_threshold": settings.retrieval_near_duplicate_threshold,
                    "top_score": round(candidates[0].score, 4) if candidates else 0.0,
                    "bottom_score": round(candidates[-1].score, 4) if candidates else 0.0,
                },
            )

        # ---------------------------------------------------------------
        # Stage 5 - optional re-ranking
        # ---------------------------------------------------------------
        if rerank_enabled and candidates:
            # Live begin: re-ranking loads a model and can take a visible second;
            # the UI marks the stage running while it happens.
            if trace:
                trace.begin("reranking")
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
    def _lexical_search(
        self,
        db: Session,
        *,
        workspace_id: int,
        question: str,
        candidates: list[RetrievedChunk],
        mode: str,
        top_k: int,
        candidate_k: int,
        document_ids: Sequence[int] | None,
        page_number: int | None,
        page_span: tuple[int, int] | None,
        section: str | None,
        trace: TraceRecorder | None,
    ) -> tuple[list[RetrievedChunk], dict[str, Any], int]:
        """Run the BM25 side, and fuse it if the mode is hybrid.

        Returns (ranked_candidates, hybrid_report, bm25_ms).

        For mode == "bm25" the candidates ARE the BM25 ranking, and the report
        still shows what dense found (an empty list, since dense did not run) so
        the lab's three columns always describe the same three methods and never
        imply a method ran when it did not.

        For mode == "hybrid" the dense list is already in `candidates` and the
        BM25 list is computed over the identical population, then fused. The
        report keeps all three rankings for the trace and the Retrieval Lab.
        """
        from app.rag.hybrid import (
            Bm25Index,
            HybridOutcome,
            bm25_corpus_for_workspace,
            reciprocal_rank_fusion,
        )

        # --- BM25 over the same scope as dense -------------------------------
        started = time.perf_counter()
        corpus = bm25_corpus_for_workspace(
            db,
            workspace_id,
            document_ids=document_ids,
            page_number=page_number,
            page_span=page_span,
            section=section,
        )
        index = Bm25Index.build(corpus, k1=settings.bm25_k1, b=settings.bm25_b)
        pool = max(candidate_k, top_k)
        hits = index.search(question, top_k=pool)
        bm25_ranked: list[RetrievedChunk] = []
        for position, (chunk, score) in enumerate(hits):
            chunk.bm25_score = round(score, 4)
            chunk.bm25_rank = position
            if mode == "bm25":
                # BM25-only: this IS the ranking score, so downstream code (dedupe,
                # top_k cut, the trace) reads one field. In hybrid mode `score` is
                # left as the chunk's cosine value, because a shared object must not
                # silently switch scale mid-pipeline.
                chunk.score = round(score, 4)
            bm25_ranked.append(chunk)
        bm25_ms = int((time.perf_counter() - started) * 1000)

        if trace:
            trace.add(
                "bm25_search",
                duration_ms=bm25_ms,
                data={
                    "algorithm": "Okapi BM25",
                    "corpus_size": len(corpus),
                    "returned": len(bm25_ranked),
                    "k1": settings.bm25_k1,
                    "b": settings.bm25_b,
                    "top_score": round(bm25_ranked[0].bm25_score or 0.0, 4)
                    if bm25_ranked
                    else 0.0,
                    "score_note": (
                        "BM25 scores are unbounded and comparable only to other "
                        "BM25 scores - never to a cosine similarity."
                    ),
                },
            )

        if mode == "bm25":
            return bm25_ranked, {"dense_only": len(candidates), "mode": "bm25"}, bm25_ms

        # --- hybrid: fuse dense (already computed) with bm25 -----------------
        for chunk in candidates:
            if chunk.original_score is None:
                chunk.original_score = chunk.score

        fused = reciprocal_rank_fusion(
            candidates,
            bm25_ranked,
            rrf_k=settings.rrf_k,
            dense_weight=settings.hybrid_dense_weight,
        )
        for position, chunk in enumerate(fused):
            chunk.rank = position

        if trace:
            trace.add(
                "rrf_fusion",
                duration_ms=0,
                data={
                    "method": "Reciprocal Rank Fusion",
                    "rrf_k": settings.rrf_k,
                    "dense_weight": settings.hybrid_dense_weight,
                    "inputs": len(candidates),
                    "outputs": len(fused),
                    "note": (
                        "Ranks are fused by position only; cosine and BM25 are "
                        "different scales and are never added together. The fused "
                        "score is a reciprocal-rank value, not a similarity."
                    ),
                },
            )

        report = HybridOutcome(
            dense=list(candidates),
            bm25=bm25_ranked,
            fused=fused,
            rrf_k=settings.rrf_k,
            dense_weight=settings.hybrid_dense_weight,
            bm25_ms=bm25_ms,
        )
        return fused, report.as_dict(), bm25_ms

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

    def _assert_workspace_has_documents(self, db: Session, workspace_id: int) -> None:
        """Distinguish "nothing matched" from "there is nothing to match against".

        These are very different for the user: one means "rephrase your question",
        the other means "upload a document first".

        Reached whenever retrieval returns no candidates - which includes a page
        filter that matched nothing, so a question about a page with no indexed
        content produces a clear message rather than a confusing empty answer.
        """
        count = db.scalar(
            select(Chunk.id).where(Chunk.workspace_id == workspace_id).limit(1)
        )
        if count is None:
            raise NoWorkspaceDocuments(
                "This workspace has no indexed documents yet. Upload a document first, "
                "then ask your question."
            )


# Guards for the containment rule. `shared >= 6` stops two chunks that merely share
# a few common words from being collapsed; `containment >= 0.92` means the shorter
# chunk is essentially a copy of, or a fragment of, the longer one.
_MIN_CONTAINMENT = 0.92
_MIN_SHARED_WORDS = 6


_LEXICAL_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with", "using",
    "is", "are", "was", "were", "be", "what", "which", "who", "when", "where",
    "tell", "me", "about", "explain", "describe", "summarize", "summarise",
    "give", "show", "list", "does", "do", "did", "say", "says", "please", "can",
    "you", "your", "this", "that", "these", "those", "it", "its", "from", "at",
    "by", "as", "how", "why", "name",
}


def _query_terms(query: str) -> set[str]:
    """Significant lowercase words from the query."""
    words = re.findall(r"[a-z0-9]+", (query or "").lower())
    return {word for word in words if len(word) > 1 and word not in _LEXICAL_STOPWORDS}


def _lexical_score(query_terms: set[str], chunk: RetrievedChunk) -> float:
    """Fraction of the query's significant terms that appear literally.

    Searched as a substring across the chunk's TEXT and its SECTION TITLE, because the
    distinctive token is often only in the heading: "iii" appears in
    "UNIT III - CONCEPT GENERATION" and may not recur in every sentence beneath it. A
    token-set intersection would fragment multi-word strings like "UNIT III" anyway.
    """
    if not query_terms:
        return 0.0

    section = str((chunk.metadata or {}).get("section") or "")
    haystack = f"{section} {chunk.content}".lower()
    if not haystack.strip():
        return 0.0

    hits = sum(1 for term in query_terms if term in haystack)
    return hits / len(query_terms)


def apply_lexical_blend(
    candidates: list[RetrievedChunk], query: str, weight: float | None = None
) -> list[RetrievedChunk]:
    """Blend a lexical term into each candidate's score, then re-sort.

    WEIGHT IS ADAPTIVE, and that is the whole point.

    A flat weight cannot serve both kinds of question:
      - "What is the name of UNIT III?" is a STRING question. The embedding barely
        distinguishes UNIT I from UNIT III, so the semantic gap is noise and lexical
        evidence must dominate.
      - "How does virtualisation save money?" is a MEANING question, where lexical
        overlap is nearly irrelevant and must stay subordinate.

    Too high breaks paraphrasing; too low fails exact references. So the weight rises
    only when the query actually names a structural reference.

    The pre-blend score is kept on `original_score`, so the trace can still show what
    retrieval believed on its own - the blend amends, it does not replace.
    """
    if not candidates or not query:
        return candidates

    terms = _query_terms(query)
    if not terms:
        return candidates

    if weight is None:
        number, _roman, _phrase = detect_unit_reference(query)
        weight = 0.55 if number else 0.15

    for chunk in candidates:
        if chunk.original_score is None:
            chunk.original_score = chunk.score
        chunk.score = round(
            (1.0 - weight) * chunk.original_score + weight * _lexical_score(terms, chunk),
            4,
        )

    candidates.sort(key=lambda chunk: chunk.score, reverse=True)
    return candidates


_LEXICAL_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with", "using",
    "is", "are", "was", "were", "be", "what", "which", "who", "when", "where",
    "tell", "me", "about", "explain", "describe", "summarize", "summarise",
    "give", "show", "list", "does", "do", "did", "say", "says", "please", "can",
    "you", "your", "this", "that", "these", "those", "it", "its", "from", "at",
    "by", "as", "how", "why", "name",
}


def _query_terms(query: str) -> set[str]:
    """Significant lowercase words from the query."""
    words = re.findall(r"[a-z0-9]+", (query or "").lower())
    return {word for word in words if len(word) > 1 and word not in _LEXICAL_STOPWORDS}


def _lexical_score(query_terms: set[str], chunk: RetrievedChunk) -> float:
    """Fraction of the query's significant terms that appear literally.

    Searched as a substring across the chunk's TEXT and its SECTION TITLE, because the
    distinctive token is often only in the heading: "iii" appears in
    "UNIT III - CONCEPT GENERATION" and may not recur in every sentence beneath it. A
    token-set intersection would fragment multi-word strings like "UNIT III" anyway.
    """
    if not query_terms:
        return 0.0

    section = str((chunk.metadata or {}).get("section") or "")
    haystack = f"{section} {chunk.content}".lower()
    if not haystack.strip():
        return 0.0

    hits = sum(1 for term in query_terms if term in haystack)
    return hits / len(query_terms)


def apply_lexical_blend(
    candidates: list[RetrievedChunk], query: str, weight: float | None = None
) -> list[RetrievedChunk]:
    """Blend a lexical term into each candidate's score, then re-sort.

    WEIGHT IS ADAPTIVE, and that is the whole point.

    A flat weight cannot serve both kinds of question:
      - "What is the name of UNIT III?" is a STRING question. The embedding barely
        distinguishes UNIT I from UNIT III, so the semantic gap is noise and lexical
        evidence must dominate.
      - "How does virtualisation save money?" is a MEANING question, where lexical
        overlap is nearly irrelevant and must stay subordinate.

    Too high breaks paraphrasing; too low fails exact references. So the weight rises
    only when the query actually names a structural reference.

    The pre-blend score is kept on `original_score`, so the trace can still show what
    retrieval believed on its own - the blend amends, it does not replace.
    """
    if not candidates or not query:
        return candidates

    terms = _query_terms(query)
    if not terms:
        return candidates

    if weight is None:
        number, _roman, _phrase = detect_unit_reference(query)
        weight = 0.55 if number else 0.15

    for chunk in candidates:
        if chunk.original_score is None:
            chunk.original_score = chunk.score
        chunk.score = round(
            (1.0 - weight) * chunk.original_score + weight * _lexical_score(terms, chunk),
            4,
        )

    candidates.sort(key=lambda chunk: chunk.score, reverse=True)
    return candidates


def _content_word_set(text: str) -> frozenset[str]:
    """Words lowercased and stripped to alphanumerics, so `user-persona` and
    `user persona` collapse to the same token."""
    return frozenset(re.findall(r"[a-z0-9]+", (text or "").lower()))


def get_retriever() -> Retriever:
    return Retriever()
