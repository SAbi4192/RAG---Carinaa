"""
Tests for the trace annotation contract Learning Mode reads.

`_annotate_trace` (app/api/routes_chat.py) is the function that decides what a
LEARNER SEES on the message-specific trace: the question, the retrieved sources,
the hybrid diagram data, and the retrieval scope. If the shape it writes changes
without the panel noticing, Learning Mode silently shows nothing - a whole
feature that disappeared behind a rename. These tests pin the exact keys.

They call `_annotate_trace` directly with fakes. The function touches no
database and makes no network call - it only reads a few attributes off its two
arguments and writes to a `TraceRecorder` it was given - so a unit test is both
possible and the right level: an HTTP-level test would seed two documents and a
workspace just to reach the same assert.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.api.routes_chat import _annotate_trace
from app.rag.retriever import RetrievalOutcome
from app.rag.trace import TraceRecorder
from app.rag.vectorstore import RetrievedChunk


def _chunk(index: int, *, name: str, section: str = "", score: float = 0.4) -> RetrievedChunk:
    return RetrievedChunk(
        vector_id=f"v{index}",
        workspace_id=1,
        document_id=1,
        chunk_id=index,
        chunk_index=index,
        content="passage text",
        metadata={"document_name": name, "section": section},
        document_name=name,
        score=score,
        rank=index,
    )


def _run(*, hybrid: dict[str, Any] | None, chunks: list[RetrievedChunk]):
    """Call _annotate_trace with just the fields it actually reads, and return
    the event data the function wrote to.
    """
    trace = TraceRecorder()
    # The pipeline records `candidate_retrieval` BEFORE the annotation runs, so
    # an existing event is a precondition, not a fixture convenience.
    trace.add("candidate_retrieval", data={"after_dedup": len(chunks)})

    outcome = RetrievalOutcome(chunks=chunks)
    outcome.hybrid = hybrid or {}
    result = SimpleNamespace(retrieval=outcome)

    ctx = SimpleNamespace(
        trace=trace,
        question="What is virtualization?",
        workspace=None,
        conversation=None,
        user_message=None,
        mode="online",
        web_sources=[],
        resolved_scope=None,
        workspace_ready=3,
        scope_names=["a.md", "b.md"],
        requested_page=None,
        page_phrase="",
        relative_note="",
        section_match=None,
        section_is_relative=None,
        conversation_history=[],
    )
    _annotate_trace(ctx, result)
    events = {event.stage: event.data for event in trace.events}
    return events["candidate_retrieval"]


def test_annotation_carries_question_and_top_sources() -> None:
    chunks = [_chunk(0, name="cloud.md", section="Virtualization", score=0.87)]
    data = _run(hybrid=None, chunks=chunks)

    assert data["question"] == "What is virtualization?"
    assert data["top_sources"], "the Learning diagram reads top_sources"
    entry = data["top_sources"][0]
    assert entry["document"] == "cloud.md"
    assert entry["section"] == "Virtualization"
    assert entry["score"] == 0.87
    # The scope keys Learning Mode's other sections depend on are present even
    # for a default workspace-scope run. `scope_file_names` is empty here BY
    # DESIGN: per-document names are only meaningful for an explicit chat scope;
    # a workspace-scope answer searched everything, so no list is attached.
    assert data["retrieval_scope"] == "workspace"
    assert data["workspace_documents_searched"] == 3
    assert data["scope_file_names"] == []


def test_annotation_attaches_hybrid_diagram_data_in_the_contract_shape() -> None:
    """The exact shape `LearningPanel.hybrid` (memo) destructures. Renaming a key
    here silently removes the section from the panel, so both sides are pinned.
    """
    hybrid = {
        "rrf_k": 60,
        "dense_weight": 0.5,
        "dense_count": 4,
        "bm25_count": 3,
        "fused_count": 5,
        "overlap_count": 2,
        "dense_only": 2,
        "bm25_only": 1,
        "dense": [
            {"vector_id": "v0", "chunk_id": 0, "document_name": "a.md", "label": "1",
             "rank": 0, "score": 0.51, "preview": "..."}
        ],
        "bm25": [
            {"vector_id": "v1", "chunk_id": 1, "document_name": "b.md", "label": "2",
             "rank": 0, "score": 6.4, "preview": "..."}
        ],
        "fused": [
            {"vector_id": "v1", "chunk_id": 1, "document_name": "b.md", "label": "2",
             "rank": 0, "score": 0.016, "preview": "..."},
            {"vector_id": "v0", "chunk_id": 0, "document_name": "a.md", "label": "1",
             "rank": 1, "score": 0.016, "preview": "..."},
        ],
    }
    data = _run(hybrid=hybrid, chunks=[_chunk(0, name="a.md"), _chunk(1, name="b.md")])

    summary = data["hybrid"]
    # Keys the frontend reads by name, exactly.
    assert summary["dense_count"] == 4
    assert summary["bm25_count"] == 3
    assert summary["overlap_count"] == 2
    assert summary["dense_only"] == 2
    assert summary["bm25_only"] == 1
    assert summary["rrf_k"] == 60
    for side in ("dense", "bm25", "fused"):
        assert isinstance(summary[side], list) and summary[side], f"{side} list must survive"
        assert set(summary[side][0]) == {"label", "document", "score"}, (
            "the per-entry shape is what the diagram maps over"
        )

    # Scores are carried through as they were on that side; a dense score is not
    # relabelled as a fused one anywhere in the summary.
    assert summary["dense"][0]["score"] == 0.51
    assert summary["fused"][0]["score"] == 0.016


def test_dense_only_run_carries_no_hybrid_section() -> None:
    """If hybrid did not run, the annotation must not fake a diagram for it. The
    panel hides the section entirely when `hybrid` is absent, which is only
    correct because the server does not include a placeholder.
    """
    data = _run(hybrid={}, chunks=[_chunk(0, name="a.md")])
    assert "hybrid" not in data
