"""
Optional re-ranking.

THE MOST IMPORTANT THING TO UNDERSTAND ABOUT RE-RANKING
-------------------------------------------------------
> Re-ranking can only REORDER the candidates that retrieval already found.
> It cannot recover evidence that vector search missed.

This is why re-ranking is OFF by default and why Basic RAG must work without it
(spec section 12). If your embedding model fails to retrieve the right chunk, a
re-ranker will never save you - it simply has nothing to promote. Re-ranking is a
precision tool applied to an already-decent candidate set, not a recall fix.

HOW IT WORKS
------------
Dense retrieval compares a single vector per chunk, which is fast but coarse - the
whole chunk is compressed into one point. A cross-encoder instead reads the query
and the chunk TOGETHER and outputs a relevance score. That is far more accurate,
and far slower: it is a forward pass per candidate, not one vector comparison.

So the pattern is:

    vector search  ->  20 cheap candidates      (recall)
    cross-encoder  ->  reorder those 20        (precision)
    keep top 5     ->  send to the LLM

We use a small cross-encoder (ms-marco-MiniLM-L-6) precisely because it has to run
on a CPU, in the request path, in Offline mode.

WHEN IT IS UNAVAILABLE
----------------------
If the cross-encoder cannot be loaded we record the stage as SKIPPED with a reason
and return the original order. We never silently pretend re-ranking happened, and
we never fabricate a re-ranked ordering.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Reranker:
    """Cross-encoder re-ranker, loaded lazily and cached process-wide."""

    _instance: "Reranker | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or settings.rerank_model
        self._model: Any = None
        self._lock = threading.Lock()
        self._load_error: str | None = None
        self._tried = False

    @classmethod
    def instance(cls) -> "Reranker":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    # ------------------------------------------------------------------- load
    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._tried and self._load_error:
            return False

        with self._lock:
            if self._model is not None:
                return True
            self._tried = True
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                logger.info("Loading re-ranker '%s'...", self.model_name)
                self._model = TextCrossEncoder(
                    model_name=self.model_name,
                    cache_dir=str(settings.embedding_cache_dir),
                )
                logger.info("Re-ranker ready.")
                return True
            except Exception as exc:
                self._load_error = f"{exc.__class__.__name__}: {exc}"
                logger.warning("Re-ranker unavailable: %s", self._load_error)
                return False

    @property
    def available(self) -> bool:
        return self._ensure_loaded()

    # ------------------------------------------------------------------ rerank
    def rerank(self, query: str, chunks: list[Any], top_n: int | None = None) -> dict[str, Any]:
        """Reorder `chunks` by cross-encoder relevance.

        Returns a dict with the reordered list plus real measurements, so the RAG
        Trace can show the before/after ranking comparison honestly.
        """
        limit = top_n or settings.rerank_top_n

        if not chunks:
            return {"chunks": [], "applied": False, "reason": "no candidates", "duration_ms": 0}

        if not self._ensure_loaded():
            return {
                "chunks": chunks,
                "applied": False,
                "reason": f"cross-encoder unavailable ({self._load_error})",
                "duration_ms": 0,
            }

        documents = [str(getattr(c, "content", "")) for c in chunks]

        start = time.perf_counter()
        try:
            with self._lock:
                scores = list(self._model.rerank(query, documents))
        except Exception as exc:
            logger.warning("Re-ranking failed: %s", exc)
            return {
                "chunks": chunks,
                "applied": False,
                "reason": f"re-rank call failed ({exc.__class__.__name__})",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        duration_ms = int((time.perf_counter() - start) * 1000)

        # Preserve the pre-rerank position and score so the UI can show movement.
        scored: list[tuple[float, int, Any]] = []
        for index, chunk in enumerate(chunks):
            raw_score = float(scores[index]) if index < len(scores) else 0.0
            if getattr(chunk, "original_score", None) is None:
                chunk.original_score = float(getattr(chunk, "score", 0.0) or 0.0)
                chunk.original_rank = int(getattr(chunk, "rank", index) or index)
            scored.append((raw_score, index, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)

        reordered = []
        for new_rank, (raw_score, _, chunk) in enumerate(scored):
            # Store the cross-encoder score separately from the cosine score: they
            # are different scales and must never be compared or mixed in a chart.
            chunk.rerank_score = round(raw_score, 6)
            chunk.rank = new_rank
            reordered.append(chunk)

        # Report how much actually moved. If nothing moved, saying so is useful.
        moved = sum(
            1
            for new_rank, (_, original_index, _) in enumerate(scored)
            if new_rank != original_index
        )

        return {
            "chunks": reordered[:limit],
            "applied": True,
            "reason": "",
            "duration_ms": duration_ms,
            "candidates": len(chunks),
            "kept": min(limit, len(reordered)),
            "moved": moved,
            "model": self.model_name,
            "comparison": [
                {
                    "chunk_id": getattr(chunk, "chunk_id", None),
                    "document_name": getattr(chunk, "document_name", ""),
                    "original_rank": getattr(chunk, "original_rank", index),
                    "reranked_rank": new_rank,
                    "cosine_score": round(float(getattr(chunk, "original_score", 0.0) or 0.0), 4),
                    "rerank_score": round(float(getattr(chunk, "rerank_score", 0.0) or 0.0), 4),
                    "moved": new_rank != getattr(chunk, "original_rank", index),
                }
                for new_rank, (_, index, chunk) in enumerate(scored)
            ],
        }

    def info(self) -> dict[str, Any]:
        return {
            "enabled": settings.rerank_enabled,
            "model": self.model_name,
            "loaded": self._model is not None,
            "available": self._model is not None or (not self._tried or not self._load_error),
            "load_error": self._load_error,
            "note": (
                "Re-ranking reorders already-retrieved candidates. It cannot recover "
                "evidence that vector search failed to retrieve."
            ),
        }


def get_reranker() -> Reranker:
    return Reranker.instance()
