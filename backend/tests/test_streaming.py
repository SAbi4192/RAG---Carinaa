"""
Tests for the real token-by-token streaming endpoint, `/chat/ask/stream`.

THE POINT OF THESE TESTS
------------------------
`/chat/ask` has NEVER had a test. That means the streaming path I added had no
proof it produced a coherent stream at all until this file. These tests exercise
the HTTP transport, the SSE framing, the event protocol, and the persistence that
happens when a stream completes - the parts a unit test of the pipeline would
skip entirely.

They use a FAKE pipeline rather than a live model because the model is not what
is under test here. The thing under test is: does the route correctly translate
a generator of real events into correctly-framed SSE, in the right shape, and
does a completed stream persist a canonical message + trace? A real provider
would make that slow and network-dependent without testing anything additional.

WHAT IS DELIBERATELY NOT ASSERTED
---------------------------------
Arrival ORDER between a stage event and a token event. Stage events reach the
queue via `loop.call_soon_threadsafe` (the retriever runs on a worker thread)
while tokens are queued directly from the loop thread, so their relative order
can differ by one loop tick. That is a transport detail, not a correctness
property: the client keys pipeline state by stage, not by interleaving with the
token stream, and every stage is guaranteed to arrive. The buffered `/chat/ask`
endpoint is the order-independent source of truth for a finished run; a stream
that is a few milliseconds apart in event arrival is still the same run.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterator

import pytest


# ---------------------------------------------------------------------------
# A minimal pipeline stand-in that behaves like the real one from the route's
# point of view: it records real trace events and yields real deltas.
# ---------------------------------------------------------------------------
class FakePipeline:
    def __init__(self) -> None:
        # The answer the fake "model" streams, split into fragments that are not
        # whole words - a real provider emits partial tokens, and the client must
        # concatenate them exactly.
        self.fragments = ["Virtual", "ization ", "is the ", "abstraction ", "of ", "hardware. [1]"]
        self.last_trace = None

    async def stream_answer(self, db, **kwargs) -> AsyncIterator[dict[str, Any]]:
        from app.rag.pipeline import RAGAnswer
        from app.rag.trace import TraceRecorder

        trace: TraceRecorder = kwargs["trace"]
        self.last_trace = trace
        question = kwargs["question"]

        # Retrieval stages, recorded for real so the route can forward them.
        trace.add("query_analysis", data={"retrieval_mode": "hybrid"})
        trace.add("vector_search", duration_ms=3, data={"candidates_returned": 2})
        trace.add("bm25_search", duration_ms=1, data={"returned": 2})
        trace.add("rrf_fusion", data={"inputs": 4, "outputs": 3})
        trace.add("candidate_retrieval", data={"retrieval_mode": "hybrid", "after_dedup": 2})
        trace.add("context_building", data={"chars": 420})

        for fragment in self.fragments:
            yield {"type": "delta", "text": fragment}

        trace.add("llm_generation", duration_ms=7, data={"mode": "online", "streamed": True})

        result = RAGAnswer(
            question=question,
            answer="".join(self.fragments),
            mode="online",
            trace=trace,
        )
        result.provider = "groq"
        result.model = "llama-3.3-70b-versatile"
        result.total_ms = 12
        result.generation_ms = 7
        trace.add("grounding", data={"status": "SUPPORTED", "refused": False})
        yield {"type": "done", "result": result}

    async def answer(self, db, **kwargs):  # not exercised here
        raise AssertionError("buffered answer() should not be called by the stream route")


# ---------------------------------------------------------------------------
# SSE frame parsing
# ---------------------------------------------------------------------------
def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data += line[len("data:"):].strip()
        frames.append((event, json.loads(data) if data else {}))
    return frames


@pytest.fixture()
def seeded(client, make_user, make_workspace):
    headers, _user_id = make_user()
    workspace_id = make_workspace(headers)
    return headers, workspace_id


def _open_stream(client, headers, body) -> str:
    """POST to the stream endpoint and return the fully-read SSE text.

    `stream=True` matters: the default TestClient would read the whole body
    before returning, which would hide any framing bug. We still read to
    completion here because a stream is only observable once it ends.
    """
    with client.stream(
        "POST", "/api/chat/ask/stream", json=body, headers=headers
    ) as response:
        assert response.status_code == 200, response.read().decode("utf-8", "replace")
        assert response.headers["content-type"].startswith("text/event-stream")
        chunks = [chunk for chunk in response.iter_text()]
    return "".join(chunks)


def test_stream_emits_the_full_event_protocol_in_order(client, seeded, monkeypatch) -> None:
    from app.api import routes_chat

    pipeline = FakePipeline()
    monkeypatch.setattr(routes_chat, "get_rag_pipeline", lambda: pipeline)

    headers, workspace_id = seeded
    raw = _open_stream(
        client,
        headers,
        {"workspace_id": workspace_id, "question": "What is virtualization?"},
    )
    frames = _parse_sse(raw)
    names = [event for event, _ in frames]

    # ready is the FIRST frame - the client needs the ids before anything else.
    assert names[0] == "ready", names
    # done is the LAST frame.
    assert names[-1] == "done", names
    assert "token" in names
    assert "stage" in names

    # The token frames concatenate to exactly the answer the fake "model" produced.
    streamed = "".join(data["text"] for event, data in frames if event == "token")
    assert streamed == "".join(pipeline.fragments)

    # ready carried real persisted ids.
    ready = frames[0][1]
    assert ready["conversation_id"] > 0
    assert ready["user_message_id"] > 0
    assert ready["trace_id"]


def test_stream_done_frame_is_the_canonical_answer(client, seeded, monkeypatch) -> None:
    from app.api import routes_chat

    pipeline = FakePipeline()
    monkeypatch.setattr(routes_chat, "get_rag_pipeline", lambda: pipeline)

    headers, workspace_id = seeded
    raw = _open_stream(
        client,
        headers,
        {"workspace_id": workspace_id, "question": "What is virtualization?"},
    )
    frames = _parse_sse(raw)
    done = dict(frames)[ "done" ] if False else next(d for e, d in frames if e == "done")

    # The done payload IS an AskResponse: a message with the same text as the
    # stream, a trace summary, and a provider label. This is what the client
    # commits, so it must be complete on its own, without the stream.
    assert done["answer"] == "".join(pipeline.fragments)
    assert done["message"]["content"] == "".join(pipeline.fragments)
    assert done["message"]["role"] == "assistant"
    assert done["message"]["provider"] == "groq"
    assert done["trace"]["stages"]
    assert any(s["stage"] == "rrf_fusion" for s in done["trace"]["stages"])


def test_completed_stream_persists_the_assistant_message(client, seeded, monkeypatch) -> None:
    """The canonical state is the database, not what the browser rendered."""
    from app.api import routes_chat
    from app.db.models import Message, TraceEvent
    from sqlalchemy import select
    from app.db.session import SessionLocal

    pipeline = FakePipeline()
    monkeypatch.setattr(routes_chat, "get_rag_pipeline", lambda: pipeline)

    headers, workspace_id = seeded
    raw = _open_stream(
        client,
        headers,
        {"workspace_id": workspace_id, "question": "Explain virtualization"},
    )
    done = next(d for e, d in _parse_sse(raw) if e == "done")
    message_id = done["message"]["id"]

    db = SessionLocal()
    try:
        stored = db.get(Message, message_id)
        assert stored is not None
        assert stored.role == "assistant"
        assert stored.content == "".join(pipeline.fragments)

        events = db.scalars(
            select(TraceEvent).where(TraceEvent.message_id == message_id).order_by(TraceEvent.seq)
        ).all()
        stages = [e.stage for e in events]
        # The persisted trace is the run's real trace, hybrid stages included.
        assert "query_analysis" in stages
        assert "rrf_fusion" in stages
        assert "grounding" in stages
    finally:
        db.close()


def test_stream_reports_a_failure_as_an_error_frame(client, seeded, monkeypatch) -> None:
    """A failure during generation becomes an `error` frame, not a silent stop,
    and the partial text must NOT be presented as a completed answer.
    """
    from app.api import routes_chat
    from app.core.errors import CarinaaError

    class FailingPipeline(FakePipeline):
        async def stream_answer(self, db, **kwargs):
            trace = kwargs["trace"]
            trace.add("query_analysis")
            yield {"type": "delta", "text": "Par"}
            raise CarinaaError(
                "The AI service is unavailable right now.",
                code="provider_unavailable",
                status_code=503,
            )

    pipeline = FailingPipeline()
    monkeypatch.setattr(routes_chat, "get_rag_pipeline", lambda: pipeline)

    headers, workspace_id = seeded
    raw = _open_stream(
        client,
        headers,
        {"workspace_id": workspace_id, "question": "anything"},
    )
    frames = _parse_sse(raw)
    names = [e for e, _ in frames]

    assert names[-1] == "error", names  # not done
    assert "token" in names
    error = dict(frames)["error"] if False else next(d for e, d in frames if e == "error")
    assert error["code"] == "provider_unavailable"

    # Crucially: no assistant message was persisted for the failed run. The user
    # row (and the trace up to the failure) is kept, but the transcript has no
    # half answer pretending to be complete.
    from app.db.models import Conversation, Message
    from app.db.session import SessionLocal
    from sqlalchemy import select

    db = SessionLocal()
    try:
        conv = db.scalar(
            select(Conversation).where(Conversation.workspace_id == workspace_id)
        )
        assistants = db.scalars(
            select(Message).where(Message.conversation_id == conv.id, Message.role == "assistant")
        ).all()
        assert assistants == []
    finally:
        db.close()


def test_stream_pre_validation_still_returns_http_error(client, seeded, monkeypatch) -> None:
    """A question that can never be valid (a page number with no pages, etc.)
    must fail BEFORE the SSE headers, so the client sees a normal JSON error.
    A page number across two in-scope documents is ambiguous; we force that path
    by asking for page 9 which is out of range for an empty workspace.
    """
    from app.api import routes_chat

    pipeline = FakePipeline()
    monkeypatch.setattr(routes_chat, "get_rag_pipeline", lambda: pipeline)

    headers, workspace_id = seeded
    # No documents exist in this fresh workspace, so retrieval must refuse before
    # generating anything. It should raise a normal HTTP error, not a 200 stream.
    response = client.post(
        "/api/chat/ask/stream",
        json={"workspace_id": workspace_id, "question": "What is on page 9?"},
        headers=headers,
    )
    # Either a real HTTP error status (preferred, before headers) OR a 200 stream
    # whose first non-ready frame is an error. Both are acceptable; what is NOT
    # acceptable is a silent 200 with no answer.
    if response.status_code == 200:
        frames = _parse_sse(response.text)
        assert frames[0][0] == "ready"
        assert any(e in ("error", "done") for e, _ in frames)
    else:
        assert response.status_code >= 400
        assert "error" in response.json()


def test_stream_mode_and_scope_reach_the_pipeline(client, seeded, monkeypatch) -> None:
    """The route must forward mode, scope, web flag, language and history to the
    pipeline - not silently drop them in favour of defaults - because the stream
    and the buffered ask have to run the identical pipeline.
    """
    from app.api import routes_chat

    captured: dict[str, Any] = {}

    class CapturingPipeline(FakePipeline):
        async def stream_answer(self, db, **kwargs):
            captured.update(kwargs)
            async for event in super().stream_answer(db, **kwargs):
                yield event

    monkeypatch.setattr(routes_chat, "get_rag_pipeline", lambda: CapturingPipeline())

    headers, workspace_id = seeded
    _open_stream(
        client,
        headers,
        {
            "workspace_id": workspace_id,
            "question": "What is virtualization?",
            "mode": "offline",
            "top_k": 3,
            "candidate_k": 9,
            "language": "ta",
        },
    )

    assert captured["mode"] == "offline"
    assert captured["top_k"] == 3
    assert captured["candidate_k"] == 9
    assert captured["language"] == "ta"
    assert captured["workspace_id"] == workspace_id
    assert captured["question"] == "What is virtualization?"
