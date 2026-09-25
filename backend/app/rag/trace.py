"""
RAG Trace - the record of what actually happened.

WHY THIS EXISTS
---------------
Spec section 22: RAG Trace must be real backend data, not a fake animation.

Every stage of the query pipeline reports into a `TraceRecorder`. The recorder
measures real durations, records real counts and real scores, and writes one
`TraceEvent` row per stage. The frontend then animates the stages *as the events
arrive* - so the animation is a rendering of reality, not a substitute for it.

If a stage is skipped (re-ranking is off by default), we say `skipped`. We never
invent a plausible-looking duration for work we did not do.

WHAT MUST NEVER BE IN A TRACE EVENT
-----------------------------------
  * API keys, tokens, passwords
  * hidden system prompts
  * private chain-of-thought
  * full document text beyond the retrieved excerpts the user can already see

`TraceRecorder.add()` runs every payload through `_sanitise()`, which enforces
this. It is a second line of defence - the first is simply not putting secrets in.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.core.logging import redact

# Field names that must never appear in a trace payload.
_FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "password_hash",
        "secret",
        "secret_key",
        "token",
        "access_token",
        "refresh_token",
        "system_prompt",
        "raw_prompt",
        "chain_of_thought",
        "reasoning",
        "internal_notes",
    }
)

_MAX_STRING = 4000


# ===========================================================================
# Status vocabulary (single source of truth)
# ===========================================================================
# Phases 23 of the brief asks for structured states rather than ad-hoc strings
# spread through the codebase. These names are also the wire contract: the SSE
# `stage` frames and stored TraceEvent rows carry exactly these values, and the
# frontend maps them to its visual states (done / active / pending / failed /
# skipped). Anything else on either side is a bug.
class StageStatus:
    RUNNING = "running"    # the stage has started and has not finished yet
    OK = "ok"              # completed successfully ("completed" on the UI)
    SKIPPED = "skipped"    # deliberately did not run
    ERROR = "error"        # failed ("failed" on the UI)


TERMINAL_STATUSES = frozenset({StageStatus.OK, StageStatus.SKIPPED, StageStatus.ERROR})


def _sanitise(value: Any, depth: int = 0) -> Any:
    """Recursively strip forbidden keys and redact secret-shaped strings."""
    if depth > 8:
        return "<truncated>"

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_KEYS:
                continue
            out[str(key)] = _sanitise(item, depth + 1)
        return out

    if isinstance(value, (list, tuple)):
        return [_sanitise(item, depth + 1) for item in value[:200]]

    if isinstance(value, str):
        text = redact(value)
        return text[:_MAX_STRING] + "..." if len(text) > _MAX_STRING else text

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    return str(value)[:200]


@dataclass
class TraceEvent:
    """One measured stage of one query."""

    seq: int
    stage: str
    status: str = "ok"  # ok | skipped | error
    duration_ms: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    # When the stage COMPLETED. Stamped as the event is finished, so a trace read back
    # later can show a real wall-clock sequence rather than only relative durations.
    # Required for the "Live RAG Trace" view, which lists events like a log:
    #
    #     16:32:01  Query processed
    #     16:32:02  Searching vector database
    #
    # Durations alone cannot produce that, because they say how long each stage took
    # but not when it happened.
    created_at: dt.datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "stage": self.stage,
            "label": self.label or _label_for(self.stage),
            "status": self.status,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "data": self.data,
        }


_STAGE_LABELS: dict[str, dict[str, Any] | str] = {
    "query_analysis": "Query Analysis",
    "query_embedding": "Query Embedding",
    "vector_search": "Vector Search",
    "bm25_search": {
        "label": "Keyword Search (BM25)",
        "what": (
            "Searches for the question's exact words, weighting rare words more "
            "than common ones. Complements the vector search, which compares meaning."
        ),
        "why": (
            "Some answers live on an exact string - a unit number, an error code, a "
            "named section. Embeddings blur near-identical words apart weakly; BM25 "
            "matches them exactly."
        ),
    },
    "rrf_fusion": {
        "label": "Fusion (RRF)",
        "what": (
            "Merges the two rankings - meaning search and keyword search - into one, "
            "using each chunk's POSITION in each list rather than its raw score. A "
            "chunk high in either list scores well; a chunk high in both wins."
        ),
        "why": (
            "Similarity (0..1) and BM25 (unbounded) are different scales. Adding their "
            "scores would silently weight whichever happens to have bigger numbers. "
            "Rank fusion never compares scales."
        ),
    },
    "candidate_retrieval": "Candidate Retrieval",
    "reranking": "Re-ranking",
    "context_building": "Context Building",
    "llm_generation": "LLM Generation",
    "grounding": "Grounding",
    "citation_resolution": "Citation Resolution",
    "web_search": "Web Search",
    "failsafe": "Offline Failsafe",
}


def _label_for(stage: str) -> str:
    """Human label for a stage, whatever shape its registry entry has.

    Most entries are a plain string. A few (the hybrid-retrieval stages) carry a
    `what`/`why` pair so the UI can explain a stage it has never seen before
    instead of hard-coding prose in two places. This function is the ONLY place
    the registry is read for a label, so adding a richer entry cannot break any
    of the existing call sites.
    """
    entry = _STAGE_LABELS.get(stage)
    if isinstance(entry, dict):
        return str(entry.get("label") or stage.replace("_", " ").title())
    if entry:
        return str(entry)
    return stage.replace("_", " ").title()


class TraceRecorder:
    """Collects real trace events for one query."""

    def __init__(self, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or uuid.uuid4().hex
        self.events: list[TraceEvent] = []
        self._started = time.perf_counter()
        self._listeners: list[Any] = []
        # When each currently-running stage started, for honest durations when the
        # completion is reported through `add()` rather than the context manager.
        self._running_clocks: dict[str, float] = {}

    # ------------------------------------------------------------- live phases
    def begin(self, stage: str, **data: Any) -> TraceEvent:
        """Record that a stage has STARTED (status="running").

        This is what makes the Learning Mode visualization show a genuinely
        ACTIVE stage rather than a timer-driven guess: the event fires the moment
        the backend enters the stage.

        Live consumers (SSE) must key steps on `seq`: the same event is broadcast
        again with a terminal status when the stage finishes (`stage()` context
        manager) or when the work is reported through `add()` for that stage.
        """
        event = TraceEvent(
            seq=len(self.events),
            stage=stage,
            status=StageStatus.RUNNING,
            duration_ms=0,
            data=_sanitise(data),
            label=_label_for(stage),
            created_at=dt.datetime.now(dt.timezone.utc),
        )
        self.events.append(event)
        self._running_clocks[stage] = time.perf_counter()
        self._broadcast(event)
        return event

    def _broadcast(self, event: TraceEvent) -> None:
        for listener in self._listeners:
            with contextlib.suppress(Exception):
                listener(event)

    # ------------------------------------------------------------- recording
    def add(
        self,
        stage: str,
        *,
        status: str = "ok",
        duration_ms: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> TraceEvent:
        # If this stage was `begin()`-ed and is still running, the report CLOSES
        # the live event instead of appending a second one, so the UI updates the
        # step it is already showing. An already-terminal event for the same stage
        # (e.g. the primary provider failed, then the fallback succeeded) stays
        # terminal and a new event follows it - that is a real second attempt, and
        # hiding it would misreport what happened.
        for candidate in reversed(self.events):
            if candidate.stage == stage and candidate.status == StageStatus.RUNNING:
                clock = self._running_clocks.pop(stage, None)
                candidate.status = status
                if duration_ms is not None:
                    candidate.duration_ms = int(duration_ms)
                elif clock is not None:
                    candidate.duration_ms = int((time.perf_counter() - clock) * 1000)
                else:
                    candidate.duration_ms = 0
                candidate.data = _sanitise(
                    {**(candidate.data or {}), **(data or {})}
                )
                candidate.created_at = dt.datetime.now(dt.timezone.utc)
                self._broadcast(candidate)
                return candidate

        event = TraceEvent(
            seq=len(self.events),
            stage=stage,
            status=status,
            duration_ms=int(duration_ms or 0),
            data=_sanitise(data or {}),
            label=_label_for(stage),
            created_at=dt.datetime.now(dt.timezone.utc),
        )
        self.events.append(event)
        self._broadcast(event)
        return event

    @contextlib.contextmanager
    def stage(self, name: str, **initial_data: Any) -> Iterator[dict[str, Any]]:
        """Time a block of work and record it as one stage.

        Usage:
            with trace.stage("vector_search", top_k=5) as info:
                results = do_search()
                info["retrieved"] = len(results)

        LIVE LIFECYCLE: the event enters the log as `running` the moment the block
        starts and is MUTATED IN PLACE to a terminal status when the block ends.
        Live subscribers (SSE) receive both broadcasts under the SAME `seq`, so a
        watching UI updates the existing step instead of appending a second one.
        Listeners must therefore key on `seq`, not assume "new seq = new event".
        A crash between the two broadcasts leaves a `running` row, which is the
        honest record: the stage started and never finished.
        """
        event = self.begin(name, **initial_data)
        start = time.perf_counter()
        status = StageStatus.OK
        try:
            yield event.data
        except Exception as exc:
            status = StageStatus.ERROR
            event.data["error"] = exc.__class__.__name__
            raise
        finally:
            event.status = status
            event.duration_ms = int((time.perf_counter() - start) * 1000)
            event.created_at = dt.datetime.now(dt.timezone.utc)
            self._broadcast(event)

    def skip(self, stage: str, reason: str, **data: Any) -> TraceEvent:
        """Record a stage that did not run. Honest, not hidden."""
        return self.add(stage, status="skipped", duration_ms=0, data={"reason": reason, **data})

    def close_running(
        self,
        stage: str,
        *,
        status: str = "error",
        data: dict[str, Any] | None = None,
    ) -> TraceEvent | None:
        """Resolve an open `begin()` event without appending a new one.

        Failure paths need this: if the stage started and the work blew up before
        any `add()` could report it, the stored trace must show a FAILED step, not
        one frozen as "running" forever. Returns None when nothing is open for
        that stage, so callers never create a duplicate event for a failure the
        adapter already recorded.
        """
        for candidate in reversed(self.events):
            if candidate.stage == stage and candidate.status == StageStatus.RUNNING:
                return self.add(stage, status=status, data=data or {})
        return None

    # -------------------------------------------------------------- listeners
    def subscribe(self, listener: Any) -> None:
        """Register a callback fired on every new event (used for live streaming)."""
        self._listeners.append(listener)

    # --------------------------------------------------------------- accessors
    @property
    def total_ms(self) -> int:
        return int((time.perf_counter() - self._started) * 1000)

    def stage_durations(self) -> dict[str, int]:
        return {e.stage: e.duration_ms for e in self.events}

    def as_dicts(self) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self.events]

    def summary(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "event_count": len(self.events),
            "total_ms": self.total_ms,
            "stages": self.as_dicts(),
        }


# Stages that are NOT part of the normal flow but can still appear in a trace.
#
# They are separated from the ordered list because a UI that drew them as ordinary
# steps would show a pipeline that never runs: `web_search` only fires when web
# search is enabled, and `failsafe` only when the offline model was unavailable and
# the extractive answer was used instead. Both REPLACE or supplement a normal step
# rather than adding a new one.
#
# They are still declared here, and the frontend must still explain them, because
# the alternative is a trace containing a stage the interface cannot label - which
# is exactly the "something is missing and you cannot tell" failure this project
# exists to prevent.
CONDITIONAL_STAGES: tuple[str, ...] = (
    "web_search",
    "failsafe",
    "bm25_search",
    "rrf_fusion",
)


def stage_definitions() -> list[dict[str, str]]:
    """Canonical stage list, served to the frontend so UI and backend agree.

    THE ORDER HERE MUST MATCH THE ORDER THE PIPELINE ACTUALLY RUNS THEM.
    This list is the contract the laboratory and the pipeline view rely on, and it
    used to be wrong: `grounding` was listed before `citation_resolution`, while
    `pipeline.py` runs citation resolution first (line ~306) and grounding second
    (line ~324). The drift checker (`scripts/check_type_drift.py`) caught it by
    comparing this list against the frontend's stage order.

    Grounding genuinely runs after citation resolution, and that ordering is
    deliberate: the grounding check needs to know which citations resolved and
    which were fabricated before it can decide whether the evidence supports the
    answer.
    """
    order = [
        "query_analysis",
        "query_embedding",
        "vector_search",
        "candidate_retrieval",
        "reranking",
        "context_building",
        "llm_generation",
        "citation_resolution",
        "grounding",
    ]
    return [
        {"stage": name, "label": _label_for(name)}
        for name in order
    ]


def conditional_stage_definitions() -> list[dict[str, str]]:
    """Stages that can appear in a trace without being part of the fixed order."""
    return [
        {
            "stage": name,
            "label": _label_for(name),
            "conditional": "true",
        }
        for name in CONDITIONAL_STAGES
    ]
