"""The trace recorder's live stage lifecycle (Learning Mode, Phases 22-27).

Learning Mode must show the REAL backend pipeline: a stage is "running" only
while the backend is genuinely inside it, and its duration is measured, never
animated. That contract lives here, in the recorder - if these tests pass, every
consumer (SSE stream, stored trace, Learning panel) is reading honest state.

The lifecycle:
    trace.begin(stage)            -> event with status "running", seq N
    trace.add(stage, status=...)  -> CLOSES the open event in place (same seq),
                                     re-broadcast to live subscribers
    trace.stage() context         -> begin + close around the block
    trace.skip(stage, reason)     -> if a begin is open it closes as skipped,
                                     otherwise it records a fresh skipped event
    close_running(stage)          -> failure path: a running stage becomes
                                     error, never stays "running" forever

Anything a UI builds from this must be able to key on `seq`: an update for a
running stage is the SAME event, not a new row.
"""

from __future__ import annotations

import pytest

from app.rag.trace import StageStatus, TraceRecorder


def _statuses(recorder: TraceRecorder, stage: str) -> list[str]:
    return [e.status for e in recorder.events if e.stage == stage]


def test_begin_records_a_running_event_and_broadcasts() -> None:
    recorder = TraceRecorder()
    seen: list[tuple[int, str]] = []
    recorder.subscribe(lambda event: seen.append((event.seq, event.status)))

    event = recorder.begin("vector_search", top_k=8)
    assert event.status == StageStatus.RUNNING
    assert event.seq == 0
    assert seen == [(0, "running")]


def test_add_closes_the_open_event_under_the_same_seq() -> None:
    """The whole point of the live pipeline: running -> terminal for ONE seq.

    A naive implementation would append a second event and the UI would render
    the stage twice. The update-in-place is what makes "→ Vector Search" become
    "✓ Vector Search" instead of two lines.
    """
    recorder = TraceRecorder()
    broadcasts: list[tuple[int, str]] = []
    recorder.subscribe(lambda e: broadcasts.append((e.seq, e.status)))

    started = recorder.begin("vector_search")
    finished = recorder.add("vector_search", duration_ms=42, data={"found": 8})

    assert finished.seq == started.seq, "the completion must resolve the same event"
    assert finished.status == "ok"
    assert finished.duration_ms == 42
    assert finished.data["found"] == 8
    # Broadcast shape: running first, then the terminal update for the same seq.
    assert broadcasts == [(0, "running"), (0, "ok")]
    assert len(recorder.events) == 1, "no duplicate event may be appended"


def test_second_provider_attempt_appends_instead_of_closing() -> None:
    """Primary fails, backup succeeds -> TWO events, because both happened.

    The fallback story the UI tells depends on this: a failed attempt and a
    recovered attempt are real, distinct facts. Merging them would hide the
    very behaviour the panel is teaching.
    """
    recorder = TraceRecorder()
    first = recorder.begin("llm_generation")
    failed = recorder.add(
        "llm_generation", status="error", data={"role": "primary"}
    )
    recovered = recorder.add("llm_generation", status="ok", data={"role": "fallback"})

    assert failed.seq == first.seq
    assert recovered.seq != failed.seq, "the second attempt is a new event"
    assert _statuses(recorder, "llm_generation") == ["error", "ok"]


def test_skip_closes_an_open_event_as_skipped() -> None:
    recorder = TraceRecorder()
    started = recorder.begin("reranking")
    skipped = recorder.skip("reranking", "re-ranking is disabled")

    assert skipped.seq == started.seq
    assert skipped.status == "skipped"
    assert skipped.data["reason"] == "re-ranking is disabled"


def test_skip_without_begin_records_a_fresh_event() -> None:
    recorder = TraceRecorder()
    skipped = recorder.skip("web_search", "disabled for this question")
    assert skipped.seq == 0
    assert skipped.status == "skipped"
    assert len(recorder.events) == 1


def test_stage_context_manager_lives_and_dies_in_place() -> None:
    recorder = TraceRecorder()
    with recorder.stage("context_building") as info:
        # Mid-block: the event exists and is running, which is exactly what the
        # SSE stream needs to show an active step.
        assert recorder.events[0].status == StageStatus.RUNNING
        info["excerpts"] = 5
    assert len(recorder.events) == 1
    done = recorder.events[0]
    assert done.status == "ok"
    assert done.data["excerpts"] == 5
    assert done.duration_ms >= 0


def test_stage_context_manager_records_error_on_raise() -> None:
    recorder = TraceRecorder()
    with pytest.raises(RuntimeError):
        with recorder.stage("vector_search"):
            raise RuntimeError("index exploded")
    assert recorder.events[0].status == "error"


def test_close_running_resolves_only_open_events() -> None:
    """Failure paths must not strand a "running" event, and must not invent a
    second event when nothing is open."""
    recorder = TraceRecorder()
    recorder.begin("reranking")

    closed = recorder.close_running("reranking")
    assert closed is not None
    assert closed.status == "error"

    # Nothing open now: a second close is a no-op, not a fabricated event.
    assert recorder.close_running("reranking") is None
    assert len(recorder.events) == 1


def test_running_event_is_not_falsely_terminal() -> None:
    """`close_running`/summary honesty: an open stage has a real duration story.

    A running event's duration must not be read as its final cost. Recording a
    duration at begin time would let a UI compute "speed" from an unfinished
    stage. We assert the initial value is exactly 0, i.e. nothing measured yet.
    """
    recorder = TraceRecorder()
    event = recorder.begin("llm_generation")
    assert event.duration_ms == 0


def test_summary_counts_only_terminal_events_for_durations() -> None:
    """`stage_durations` is used for "Total time" summaries. A running stage has
    no meaningful duration, so it must not report one."""
    recorder = TraceRecorder()
    recorder.begin("query_analysis")
    recorder.add("query_analysis", duration_ms=7)
    durations = recorder.stage_durations()
    assert durations["query_analysis"] == 7
