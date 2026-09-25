"""Analytics routes - real aggregates from recorded queries."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFound
from app.core.logging import get_logger
from app.core.sanitize import public_provenance
from app.db.models import Conversation, Document, Message, QueryLog, Workspace
from app.evaluation.metrics import evaluate_generation
from app.rag.grounding import STATUS_LABELS

logger = get_logger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])


def _require_workspace(db: DbSession, user: CurrentUser, workspace_id: int) -> Workspace:
    workspace = db.scalar(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.user_id == user.id)
    )
    if workspace is None:
        raise NotFound("That workspace does not exist.")
    return workspace


def percentile(values: list[int], fraction: float) -> float:
    """Nearest-rank percentile over an ALREADY SORTED list.

    Deliberately naive (no interpolation): with the sample sizes this analytics page
    sees, an interpolated p95 would imply a precision the data does not have. Callers
    sort once and ask for both p50 and p95 from the same list.
    """
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(len(values) * fraction) - 1))
    return float(values[index])


@router.get("/overview")
def overview(
    user: CurrentUser,
    db: DbSession,
    workspace_id: int | None = Query(default=None),
) -> dict:
    """Headline numbers for the Analytics page. Every value is a query result."""
    workspace_ids = (
        [workspace_id]
        if workspace_id is not None
        else list(db.scalars(select(Workspace.id).where(Workspace.user_id == user.id)).all())
    )
    if workspace_id is not None:
        _require_workspace(db, user, workspace_id)

    if not workspace_ids:
        return {
            "workspaces": 0,
            "documents": 0,
            "chunks": 0,
            "conversations": 0,
            "queries": 0,
            "web_searches": 0,
            "avg_total_ms": 0,
            "grounding": {status: 0 for status in STATUS_LABELS},
            "note": "No workspaces yet. Create one and upload a document to see analytics.",
        }

    from app.db.models import Chunk

    documents = int(
        db.scalar(
            select(func.count()).select_from(Document).where(Document.workspace_id.in_(workspace_ids))
        )
        or 0
    )
    chunks = int(
        db.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.workspace_id.in_(workspace_ids))
        )
        or 0
    )
    conversations = int(
        db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.workspace_id.in_(workspace_ids))
        )
        or 0
    )
    queries = int(
        db.scalar(
            select(func.count()).select_from(QueryLog).where(QueryLog.workspace_id.in_(workspace_ids))
        )
        or 0
    )

    grounding_rows = dict(
        db.execute(
            select(QueryLog.grounding_status, func.count())
            .where(QueryLog.workspace_id.in_(workspace_ids))
            .group_by(QueryLog.grounding_status)
        ).all()
    )

    # WHY the web-search count is read from `messages` and not from `query_logs`:
    # QueryLog has no web_search_used column, and the flag has to be a real one -
    # so it is counted from the assistant Message rows, which already record it.
    # The join chain is the ownership path: messages -> conversations -> workspaces.
    web_searches = int(
        db.scalar(
            select(func.count())
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.workspace_id.in_(workspace_ids),
                Message.role == "assistant",
                Message.web_search_used.is_(True),
            )
        )
        or 0
    )

    avg_total_ms = int(
        round(
            float(
                db.scalar(
                    select(func.avg(QueryLog.total_ms)).where(
                        QueryLog.workspace_id.in_(workspace_ids)
                    )
                )
                or 0.0
            )
        )
    )

    return {
        "workspaces": len(workspace_ids),
        "documents": documents,
        "chunks": chunks,
        "conversations": conversations,
        "queries": queries,
        "web_searches": web_searches,
        "avg_total_ms": avg_total_ms,
        "grounding": {status: int(grounding_rows.get(status, 0)) for status in STATUS_LABELS},
        "grounding_labels": STATUS_LABELS,
    }


@router.get("/retrieval")
def retrieval_metrics(
    user: CurrentUser,
    db: DbSession,
    workspace_id: int | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    """Retrieval and generation quality measured from recorded queries."""
    statement = select(QueryLog).where(QueryLog.user_id == user.id)
    if workspace_id is not None:
        _require_workspace(db, user, workspace_id)
        statement = statement.where(QueryLog.workspace_id == workspace_id)
    statement = statement.order_by(QueryLog.created_at.desc()).limit(limit)

    logs = list(db.scalars(statement).all())
    if not logs:
        return {
            "total": 0,
            "note": "No queries have been recorded yet. Ask a question to populate this page.",
        }

    # Join in the answer text (and the question that preceded it) so the
    # answer-relevance proxy has something to work with.
    message_ids = [log.message_id for log in logs if log.message_id]
    messages = {
        message.id: message
        for message in db.scalars(
            select(Message).where(Message.id.in_(message_ids or [0]))
        ).all()
    }

    rows: list[dict] = []
    for log in logs:
        message = messages.get(log.message_id) if log.message_id else None
        rows.append(
            {
                "grounding_status": log.grounding_status,
                "refused": log.refused,
                "citation_count": log.citation_count,
                "used_count": log.used_count,
                "retrieved_count": log.retrieved_count,
                "top_score": log.top_score,
                "mean_score": log.mean_score,
                "total_ms": log.total_ms,
                "retrieval_ms": log.retrieval_ms,
                "generation_ms": log.generation_ms,
                "provider": log.provider,
                "model": log.model,
                "ai_mode": log.ai_mode,
                "answer": message.content if message else "",
                "question": _question_for(db, message) if message else "",
            }
        )

    metrics = evaluate_generation(rows)

    latencies = sorted(row["total_ms"] for row in rows if row["total_ms"])
    retrieval_latencies = sorted(row["retrieval_ms"] for row in rows if row["retrieval_ms"])

    provider_counts: dict[str, int] = {}
    mode_counts: dict[str, int] = {}
    for row in rows:
        # Aggregate by PUBLIC role, never by cloud vendor: analytics is a user-facing
        # view, so "groq: 12" must never appear. The QueryLog keeps the real
        # provenance for developers who query the database directly.
        _public_provider, _public_model = public_provenance(row["provider"], row["model"])
        if (row["model"] or "") == "extractive":
            key = "Extractive (no model)"
        elif _public_provider == "local":
            key = "Local model"
        else:
            key = "Remote answer engine"
        provider_counts[key] = provider_counts.get(key, 0) + 1
        mode_counts[row["ai_mode"]] = mode_counts.get(row["ai_mode"], 0) + 1

    # Which documents actually backed these answers, counted from the stored
    # citations rather than from uploads: a file that never contributed to an
    # answer must not show up here as "used". Web citations are excluded -
    # "document usage" means your knowledge, not the internet.
    #
    # The web-search flag lives on Message, not QueryLog (adding a column would
    # be a schema change), so it is read from the same rows already loaded.
    document_stats: dict[str, dict[str, int]] = {}
    web_searches = 0
    for message in messages.values():
        if message.web_search_used:
            web_searches += 1
        cited_names = {
            str(citation.get("document_name") or "")
            for citation in (message.citations or [])
            if isinstance(citation, dict)
            and citation.get("kind", "document") == "document"
            and citation.get("document_name")
        }
        for name in cited_names:
            stats = document_stats.setdefault(name, {"citations": 0, "queries": 0})
            stats["citations"] += sum(
                1
                for citation in (message.citations or [])
                if isinstance(citation, dict)
                and citation.get("kind", "document") == "document"
                and str(citation.get("document_name") or "") == name
            )
            stats["queries"] += 1

    top_documents = sorted(
        (
            {"name": name, "citations": stats["citations"], "queries": stats["queries"]}
            for name, stats in document_stats.items()
        ),
        key=lambda item: (-item["citations"], -item["queries"], item["name"]),
    )[:10]

    return {
        **metrics,
        "latency": {
            "p50_ms": percentile(latencies, 0.5),
            "p95_ms": percentile(latencies, 0.95),
            "max_ms": latencies[-1] if latencies else 0,
            "retrieval_p50_ms": percentile(retrieval_latencies, 0.5),
            "retrieval_p95_ms": percentile(retrieval_latencies, 0.95),
        },
        "providers": provider_counts,
        "modes": mode_counts,
        "documents": top_documents,
        "web_searches": web_searches,
        "recent": [
            {
                "provider": public_provenance(row["provider"], row["model"])[0],
                "ai_mode": row["ai_mode"],
                "grounding_status": row["grounding_status"],
                "top_score": round(row["top_score"], 4),
                "retrieved": row["retrieved_count"],
                "used": row["used_count"],
                "citations": row["citation_count"],
                "total_ms": row["total_ms"],
            }
            for row in rows[:25]
        ],
    }


def _question_for(db: DbSession, message: Message) -> str:
    """The user question that immediately preceded this assistant message."""
    previous = db.scalars(
        select(Message)
        .where(
            Message.conversation_id == message.conversation_id,
            Message.role == "user",
            Message.created_at <= message.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    ).first()
    return previous.content if previous else ""


@router.get("/activity")
def activity(
    user: CurrentUser,
    db: DbSession,
    workspace_id: int | None = Query(default=None),
    days: int = Query(default=14, ge=1, le=90),
) -> dict:
    """Daily query counts, for the activity chart. Real timestamps only."""
    import datetime as dt

    statement = select(QueryLog).where(QueryLog.user_id == user.id)
    if workspace_id is not None:
        _require_workspace(db, user, workspace_id)
        statement = statement.where(QueryLog.workspace_id == workspace_id)

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    logs = list(db.scalars(statement.where(QueryLog.created_at >= since)).all())

    # One extra query resolves the web-search flag for this window: QueryLog carries no
    # web_search_used column, so it is read back from the assistant Message each log
    # points at. An id that is absent (or null) counts as False rather than guessing.
    log_message_ids = [log.message_id for log in logs if log.message_id]
    web_message_ids = set(
        db.scalars(
            select(Message.id).where(
                Message.id.in_(log_message_ids or [0]),
                Message.role == "assistant",
                Message.web_search_used.is_(True),
            )
        ).all()
    )

    buckets: dict[str, dict[str, int]] = {}
    day_total_ms: dict[str, int] = {}
    day_web_searches: dict[str, int] = {}
    today = dt.datetime.now(dt.timezone.utc).date()
    for offset in range(days - 1, -1, -1):
        day = (today - dt.timedelta(days=offset)).isoformat()
        buckets[day] = {"queries": 0, "refusals": 0, "citation_errors": 0}

    for log in logs:
        created = log.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=dt.timezone.utc)
        day = created.date().isoformat()
        if day not in buckets:
            continue
        buckets[day]["queries"] += 1
        if log.refused:
            buckets[day]["refusals"] += 1
        if log.grounding_status == "CITATION_ERROR":
            buckets[day]["citation_errors"] += 1
        day_total_ms[day] = day_total_ms.get(day, 0) + int(log.total_ms or 0)
        if log.message_id in web_message_ids:
            day_web_searches[day] = day_web_searches.get(day, 0) + 1

    return {
        "days": days,
        "series": [
            {
                "date": day,
                **values,
                # A day with no queries averages to 0, not to an empty/None value the
                # chart would have to special-case.
                "avg_ms": int(round(day_total_ms.get(day, 0) / values["queries"]))
                if values["queries"]
                else 0,
                "web_searches": day_web_searches.get(day, 0),
            }
            for day, values in sorted(buckets.items())
        ],
    }


@router.get("/stages")
def stage_metrics(
    user: CurrentUser,
    db: DbSession,
    workspace_id: int | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=2000, ge=1, le=20000),
) -> dict:
    """Per-stage timings aggregated from the trace events the pipeline recorded.

    These are the durations RAG Trace already shows one query at a time, summed up.
    Nothing here is derived from a vendor's reported latency - each event is a stage
    the pipeline actually ran and timed with `perf_counter`.
    """
    import datetime as dt

    from app.db.models import TraceEvent
    from app.rag.trace import conditional_stage_definitions, stage_definitions

    workspace_ids = (
        [workspace_id]
        if workspace_id is not None
        else list(db.scalars(select(Workspace.id).where(Workspace.user_id == user.id)).all())
    )
    if workspace_id is not None:
        _require_workspace(db, user, workspace_id)

    if not workspace_ids:
        return {
            "total_queries": 0,
            "stages": [],
            "note": "No traces recorded yet. Ask a question in Chat and this fills in.",
        }

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    # Ownership path: trace_events -> messages -> conversations -> workspaces, the same
    # chain the security report uses. Filtering on the workspace is what keeps one
    # user's stage timings out of another user's response.
    rows = db.execute(
        select(
            TraceEvent.stage,
            TraceEvent.status,
            TraceEvent.duration_ms,
            TraceEvent.trace_id,
        )
        .join(Message, Message.id == TraceEvent.message_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.workspace_id.in_(workspace_ids),
            TraceEvent.created_at >= since,
        )
        # `limit` bounds EVENTS, so newest-first keeps the most recent traces whole
        # instead of truncating them at the cut-off.
        .order_by(TraceEvent.id.desc())
        .limit(limit)
    ).all()

    if not rows:
        return {
            "total_queries": 0,
            "stages": [],
            "note": "No traces recorded yet. Ask a question in Chat and this fills in.",
        }

    durations: dict[str, list[int]] = {}
    skipped_counts: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    trace_totals: dict[str, int] = {}

    for row in rows:
        duration = int(row.duration_ms or 0)
        # Per-trace totals feed avg_total_ms. A skipped event carries 0 ms, so including
        # it here changes nothing.
        trace_totals[row.trace_id] = trace_totals.get(row.trace_id, 0) + duration
        if row.status == "skipped":
            # A skipped stage performed no work, so it must not drag the average down.
            # It is still reported, in `skipped`, because hiding it is the failure this
            # project exists to prevent.
            skipped_counts[row.stage] = skipped_counts.get(row.stage, 0) + 1
            continue
        if row.status == "error":
            error_counts[row.stage] = error_counts.get(row.stage, 0) + 1
        durations.setdefault(row.stage, []).append(duration)

    # Labels come from the trace module so the UI and the backend cannot drift apart.
    present = set(durations) | set(skipped_counts) | set(error_counts)
    ordered = [item["stage"] for item in stage_definitions()]
    ordered += [
        item["stage"] for item in conditional_stage_definitions() if item["stage"] in present
    ]
    # An unrecognised stage is still real data; dropping it would be worse than a
    # title-cased fallback label.
    ordered += sorted(stage for stage in present if stage not in ordered)
    labels = {item["stage"]: item["label"] for item in stage_definitions()}
    labels.update({item["stage"]: item["label"] for item in conditional_stage_definitions()})

    stages: list[dict] = []
    for stage in ordered:
        values = sorted(durations.get(stage, []))
        total = sum(values)
        stages.append(
            {
                "stage": stage,
                "label": labels.get(stage, stage.replace("_", " ").title()),
                "count": len(values),
                "avg_ms": round(total / len(values), 1) if values else 0.0,
                "p50_ms": int(round(percentile(values, 0.5))),
                "p95_ms": int(round(percentile(values, 0.95))),
                "total_ms": int(total),
                "skipped": int(skipped_counts.get(stage, 0)),
                "errors": int(error_counts.get(stage, 0)),
            }
        )

    trace_count = len(trace_totals)
    return {
        "total_queries": trace_count,
        "avg_total_ms": int(round(sum(trace_totals.values()) / trace_count)) if trace_count else 0,
        "stages": stages,
        "note": "Measured from the trace events the pipeline recorded for each answer.",
    }

