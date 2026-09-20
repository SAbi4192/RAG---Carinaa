"""Analytics routes - real aggregates from recorded queries."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFound
from app.core.logging import get_logger
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

    return {
        "workspaces": len(workspace_ids),
        "documents": documents,
        "chunks": chunks,
        "conversations": conversations,
        "queries": queries,
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

    def percentile(values: list[int], fraction: float) -> float:
        if not values:
            return 0.0
        index = min(len(values) - 1, max(0, int(len(values) * fraction) - 1))
        return float(values[index])

    provider_counts: dict[str, int] = {}
    mode_counts: dict[str, int] = {}
    for row in rows:
        key = row["provider"] or "unknown"
        provider_counts[key] = provider_counts.get(key, 0) + 1
        mode_counts[row["ai_mode"]] = mode_counts.get(row["ai_mode"], 0) + 1

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
        "recent": [
            {
                "provider": row["provider"],
                "model": row["model"],
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

    buckets: dict[str, dict[str, int]] = {}
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

    return {
        "days": days,
        "series": [{"date": day, **values} for day, values in sorted(buckets.items())],
    }
