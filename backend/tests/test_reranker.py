"""Re-ranking: the safety guarantees, and the parts that are easy to get wrong.

Re-ranking is OFF by default (`RERANK_ENABLED=false`), which means this code path is
not exercised by the normal test run or the end-to-end script. An unexercised path is
where bugs hide, so these tests drive it directly with a stub cross-encoder.

The two things that must never happen:

  1. **A missing or broken re-ranker must not break retrieval.** Re-ranking is an
     optional refinement. If the model is not on disk, retrieval must return exactly
     what dense search found, in the same order, and say so. A degraded feature is
     acceptable; a broken pipeline is not.

  2. **The two scores must never be confused.** The cross-encoder score and the cosine
     similarity are on different, incomparable scales. Mixing them in a chart or
     comparing them numerically produces confident nonsense.

The honest-reporting rule also applies here: the result says whether re-ranking was
actually applied, and why not when it was not.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import settings
from app.rag.reranker import Reranker


class _Chunk:
    """Minimal stand-in for a retrieved chunk."""

    def __init__(self, chunk_id: int, content: str, score: float, rank: int) -> None:
        self.chunk_id = chunk_id
        self.content = content
        self.score = score
        self.rank = rank
        self.document_name = f"doc{chunk_id}.md"


class _StubCrossEncoder:
    """A cross-encoder that scores by keyword presence, so ordering is deterministic."""

    def __init__(self, scores: list[float] | None = None) -> None:
        self._scores = scores

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if self._scores is not None:
            return self._scores
        # Score higher when the document mentions a word from the query.
        words = {w.lower() for w in query.split()}
        return [float(len(words & set(d.lower().split()))) for d in documents]


class _ExplodingCrossEncoder:
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        raise RuntimeError("model crashed mid-inference")


def _unavailable_reranker() -> Reranker:
    """A reranker whose model failed to load, as happens with no network."""
    reranker = Reranker()
    reranker._tried = True
    reranker._load_error = "simulated: model not on disk"
    return reranker


def _loaded_reranker(model: Any) -> Reranker:
    reranker = Reranker()
    reranker._tried = True
    reranker._model = model
    return reranker


# ---------------------------------------------------------------------------
# Guarantee 1 - degrade, never break
# ---------------------------------------------------------------------------
def test_unavailable_reranker_returns_candidates_unchanged() -> None:
    chunks = [_Chunk(1, "alpha", 0.9, 0), _Chunk(2, "beta", 0.5, 1)]
    result = _unavailable_reranker().rerank("what is alpha?", chunks)

    assert result["applied"] is False
    assert result["chunks"] is chunks, "the original list must be passed through"
    assert [c.chunk_id for c in result["chunks"]] == [1, 2], "order must not change"
    assert "unavailable" in result["reason"]


def test_unavailable_reranker_reports_why() -> None:
    """A silent fallback would hide a broken deployment."""
    result = _unavailable_reranker().rerank("q", [_Chunk(1, "a", 0.5, 0)])
    assert result["reason"], "the reason must be populated, not empty"
    assert "simulated" in result["reason"]


def test_empty_candidates_are_safe() -> None:
    result = _loaded_reranker(_StubCrossEncoder()).rerank("q", [])
    assert result["applied"] is False
    assert result["chunks"] == []
    assert result["duration_ms"] == 0


def test_a_crashing_model_does_not_break_retrieval() -> None:
    """An exception inside the model must not propagate to the request."""
    chunks = [_Chunk(1, "alpha", 0.9, 0), _Chunk(2, "beta", 0.5, 1)]
    result = _loaded_reranker(_ExplodingCrossEncoder()).rerank("q", chunks)

    assert result["applied"] is False
    assert [c.chunk_id for c in result["chunks"]] == [1, 2]
    assert "failed" in result["reason"]


# ---------------------------------------------------------------------------
# Guarantee 2 - the two score scales stay separate
# ---------------------------------------------------------------------------
def test_successful_rerank_reorders_by_cross_encoder_score() -> None:
    # Dense order is 1, 2, 3; the cross-encoder prefers 3, then 1, then 2.
    chunks = [
        _Chunk(1, "alpha", 0.90, 0),
        _Chunk(2, "beta", 0.80, 1),
        _Chunk(3, "gamma", 0.70, 2),
    ]
    reranker = _loaded_reranker(_StubCrossEncoder(scores=[0.5, 0.1, 0.9]))
    result = reranker.rerank("q", chunks, top_n=3)

    assert result["applied"] is True
    assert [c.chunk_id for c in result["chunks"]] == [3, 1, 2]


def test_original_rank_and_score_are_preserved_for_comparison() -> None:
    """The UI shows movement, which requires remembering where things started."""
    chunks = [_Chunk(1, "alpha", 0.90, 0), _Chunk(2, "beta", 0.80, 1)]
    reranker = _loaded_reranker(_StubCrossEncoder(scores=[0.1, 0.9]))
    result = reranker.rerank("q", chunks, top_n=2)

    moved = {row["chunk_id"]: row for row in result["comparison"]}
    assert moved[1]["original_rank"] == 0
    assert moved[1]["reranked_rank"] == 1
    assert moved[1]["cosine_score"] == 0.9, "the dense score must survive re-ranking"
    assert moved[1]["moved"] is True


def test_rerank_and_cosine_scores_are_never_conflated() -> None:
    """Different scales. They must stay in separate fields."""
    chunks = [_Chunk(1, "alpha", 0.90, 0)]
    reranker = _loaded_reranker(_StubCrossEncoder(scores=[0.123456]))
    result = reranker.rerank("q", chunks, top_n=1)

    row = result["comparison"][0]
    assert row["cosine_score"] == 0.9
    assert row["rerank_score"] == 0.1235, "the cross-encoder score is its own value"
    assert result["chunks"][0].rerank_score == 0.123456
    assert result["chunks"][0].score == 0.90, "cosine score is untouched"


def test_rerank_narrows_candidates_to_top_n() -> None:
    """This is the precision step: recall widened the pool, re-ranking narrows it."""
    chunks = [_Chunk(i, f"c{i}", 0.9 - i * 0.1, i) for i in range(1, 6)]
    reranker = _loaded_reranker(_StubCrossEncoder())
    result = reranker.rerank("q", chunks, top_n=2)

    assert result["candidates"] == 5
    assert result["kept"] == 2
    assert len(result["chunks"]) == 2


def test_nothing_moved_is_reported_honestly() -> None:
    """If re-ranking changed nothing, saying so is more useful than staying quiet."""
    chunks = [_Chunk(1, "alpha", 0.9, 0), _Chunk(2, "beta", 0.8, 1)]
    reranker = _loaded_reranker(_StubCrossEncoder(scores=[0.9, 0.1]))
    result = reranker.rerank("q", chunks, top_n=2)

    assert result["applied"] is True
    assert result["moved"] == 0


def test_reranking_is_disabled_by_default() -> None:
    """Basic RAG before advanced RAG - and the default must match the docs."""
    assert settings.rerank_enabled is False


@pytest.mark.parametrize("top_n", [1, 3])
def test_top_n_never_exceeds_available_candidates(top_n: int) -> None:
    chunks = [_Chunk(1, "alpha", 0.9, 0)]
    result = _loaded_reranker(_StubCrossEncoder()).rerank("q", chunks, top_n=top_n)
    assert len(result["chunks"]) == 1
