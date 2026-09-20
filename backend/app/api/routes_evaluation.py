"""
Evaluation routes.

Two different kinds of measurement live here:

  /evaluation/security    runs real security probes against the live system
  /evaluation/retrieval   computes Recall@K / Precision@K / MRR from a labelled set

The second one needs a labelled dataset. There is no way to compute Recall@K
without knowing which chunks are actually relevant - that is a human judgement.
So the endpoint accepts a dataset and refuses to invent one. If you post an empty
dataset you get a clear message, not a score of 0.87.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.errors import CarinaaError, NotFound
from app.core.logging import get_logger
from app.db.models import (
    Chunk,
    Conversation,
    EvaluationRun,
    Message,
    TraceEvent,
    User,
    Workspace,
)
from app.evaluation.metrics import evaluate_retrieval
from app.evaluation.security import run_all as run_security_checks
from app.rag.retriever import get_retriever

logger = get_logger(__name__)
router = APIRouter(prefix="/evaluation", tags=["evaluation"])


def _require_workspace(db: DbSession, user: CurrentUser, workspace_id: int) -> Workspace:
    workspace = db.scalar(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.user_id == user.id)
    )
    if workspace is None:
        raise NotFound("That workspace does not exist.")
    return workspace


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
@router.get("/security")
def security_report(user: CurrentUser, db: DbSession) -> dict:
    """Run the security self-tests against the live system.

    The cross-workspace checks need two workspaces to compare. If the user only has
    one, the check reports SKIPPED with the reason - it does not silently pass.
    """
    workspaces = list(
        db.scalars(
            select(Workspace).where(Workspace.user_id == user.id).order_by(Workspace.id)
        ).all()
    )

    workspace_a = workspaces[0].id if workspaces else None
    workspace_b = workspaces[1].id if len(workspaces) > 1 else None

    # Real trace payloads from THIS user's recent answers, so the secret scan is
    # testing actual data rather than a fixture.
    #
    # The join chain is the ownership path:
    #     trace_events -> messages -> conversations -> users
    # Scoping on Conversation.user_id is what keeps this to the caller's own data.
    # Without it the query reads every user's traces, which is both a privacy leak
    # and a meaningless test (it would scan other tenants' payloads).
    trace_payloads = list(
        db.scalars(
            select(TraceEvent.event_data)
            .join(Message, Message.id == TraceEvent.message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.user_id == user.id)
            .order_by(TraceEvent.id.desc())
            .limit(400)
        ).all()
    )
    trace_payloads = [payload for payload in trace_payloads if isinstance(payload, dict)]

    # The authorization check needs a second, REAL account to act as the attacker:
    # "can somebody else load my workspace?" is only a meaningful question if that
    # somebody exists. If this is the only account in the database, the check
    # reports SKIPPED with the reason instead of passing by default.
    attacker_id = db.scalar(
        select(User.id).where(User.id != user.id).order_by(User.id).limit(1)
    )

    report = run_security_checks(
        db=db,
        owner_id=user.id,
        attacker_id=attacker_id,
        workspace_a=workspace_a,
        workspace_b=workspace_b,
        trace_payloads=trace_payloads,
    )

    report["context"] = {
        "workspaces_available": len(workspaces),
        "workspace_a": workspace_a,
        "workspace_b": workspace_b,
        "trace_payloads_scanned": len(trace_payloads),
        # A boolean, not the other account's id - the report should not disclose
        # another user's identity.
        "second_account_available": attacker_id is not None,
        "hint": (
            "Create a second workspace to enable the cross-workspace isolation check."
            if len(workspaces) < 2
            else ""
        ),
    }
    return report


# ---------------------------------------------------------------------------
# Retrieval evaluation
# ---------------------------------------------------------------------------
class LabelledQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    relevant_chunk_ids: list[int] = Field(default_factory=list)
    relevant_document_ids: list[int] = Field(default_factory=list)


class RetrievalEvaluationRequest(BaseModel):
    workspace_id: int
    dataset: list[LabelledQuestion] = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=50)
    candidate_k: int = Field(default=20, ge=1, le=200)
    use_rerank: bool | None = None
    name: str = Field(default="Retrieval evaluation", max_length=200)


@router.post("/retrieval")
async def retrieval_evaluation(
    payload: RetrievalEvaluationRequest, user: CurrentUser, db: DbSession
) -> dict:
    """Compute Recall@K, Precision@K and MRR against a labelled dataset.

    The dataset must supply `relevant_chunk_ids` per question. Those ids come from
    the Document Viewer page, where the operator can read each chunk and decide.
    """
    workspace = _require_workspace(db, user, payload.workspace_id)

    retriever = get_retriever()
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for item in payload.dataset:
        relevant = list(item.relevant_chunk_ids)

        # Allow labelling by document instead of chunk: in that case every chunk of
        # that document counts as relevant. Useful for a quick sanity check.
        if not relevant and item.relevant_document_ids:
            relevant = list(
                db.scalars(
                    select(Chunk.id).where(
                        Chunk.workspace_id == workspace.id,
                        Chunk.document_id.in_(item.relevant_document_ids),
                    )
                ).all()
            )

        if not relevant:
            skipped.append(
                {
                    "question": item.question[:120],
                    "reason": (
                        "No relevant chunk ids were supplied, so Recall@K cannot be "
                        "computed for this question."
                    ),
                }
            )
            continue

        try:
            outcome = retriever.retrieve(
                db,
                workspace_id=workspace.id,
                question=item.question,
                top_k=max(payload.k, 10),
                candidate_k=max(payload.candidate_k, payload.k),
                use_rerank=payload.use_rerank,
            )
        except Exception as exc:
            skipped.append(
                {"question": item.question[:120], "reason": f"Retrieval failed: {exc}"}
            )
            continue

        results.append(
            {
                "question": item.question,
                "retrieved_ids": [c.chunk_id for c in outcome.chunks if c.chunk_id],
                "relevant_ids": relevant,
            }
        )

    if not results:
        raise CarinaaError(
            "None of the supplied questions could be evaluated. Each one needs at least "
            "one relevant chunk id - look them up in the Document Viewer.",
            code="empty_evaluation_dataset",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"skipped": skipped},
        )

    evaluation = evaluate_retrieval(results, k=payload.k)

    run = EvaluationRun(
        workspace_id=workspace.id,
        user_id=user.id,
        name=payload.name,
        k=payload.k,
        dataset_size=evaluation.dataset_size,
        metrics={
            **evaluation.as_dict(),
            "skipped": skipped,
            "candidate_k": payload.candidate_k,
            "rerank": bool(payload.use_rerank),
        },
        details=evaluation.details,
        embedding_model=settings.embedding_model,
        rerank_enabled=bool(payload.use_rerank),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    return {
        "run_id": run.id,
        "name": run.name,
        "workspace_id": workspace.id,
        **evaluation.as_dict(),
        "skipped": skipped,
    }


@router.get("/history")
def evaluation_history(
    user: CurrentUser,
    db: DbSession,
    workspace_id: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    statement = select(EvaluationRun).where(EvaluationRun.user_id == user.id)
    if workspace_id is not None:
        _require_workspace(db, user, workspace_id)
        statement = statement.where(EvaluationRun.workspace_id == workspace_id)
    statement = statement.order_by(EvaluationRun.created_at.desc()).limit(limit)

    runs = db.scalars(statement).all()
    return {
        "runs": [
            {
                "id": run.id,
                "name": run.name,
                "workspace_id": run.workspace_id,
                "k": run.k,
                "dataset_size": run.dataset_size,
                "metrics": run.metrics or {},
                "embedding_model": run.embedding_model,
                "rerank_enabled": run.rerank_enabled,
                "created_at": run.created_at,
            }
            for run in runs
        ],
        "total": len(runs),
    }


@router.delete("/history/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_evaluation_run(run_id: int, user: CurrentUser, db: DbSession) -> None:
    run = db.scalar(
        select(EvaluationRun).where(EvaluationRun.id == run_id, EvaluationRun.user_id == user.id)
    )
    if run is None:
        raise NotFound("That evaluation run does not exist.")
    db.delete(run)
    db.commit()


# ---------------------------------------------------------------------------
# Reference: what each metric means
# ---------------------------------------------------------------------------
@router.get("/metrics-reference")
def metrics_reference(_: CurrentUser) -> dict:
    return {
        "retrieval": [
            {
                "metric": "Recall@K",
                "definition": "Of the chunks a human marked relevant, what share appear in the top K?",
                "why": "The most important retrieval metric. If the evidence is not retrieved, no amount of re-ranking or prompt engineering can recover it.",
            },
            {
                "metric": "Precision@K",
                "definition": "Of the top K chunks returned, what share are actually relevant?",
                "why": "Measures how much noise is being sent to the language model, which wastes context budget.",
            },
            {
                "metric": "MRR",
                "definition": "Mean of 1/rank of the first relevant chunk.",
                "why": "Rewards putting the right answer near the top. Position 1 and position 8 score very differently.",
            },
            {
                "metric": "MAP",
                "definition": "Mean average precision across all relevant hits.",
                "why": "A rank-aware summary that rewards ordering all relevant chunks highly.",
            },
        ],
        "generation": [
            {
                "metric": "Faithfulness",
                "definition": "Share of answers whose grounding verdict was SUPPORTED.",
                "why": "A proxy for hallucination rate, measured against retrieved evidence.",
            },
            {
                "metric": "Citation correctness",
                "definition": "Share of answers containing no fabricated citation.",
                "why": "A fabricated citation is worse than no citation, because it looks verifiable and is not.",
            },
            {
                "metric": "Refusal accuracy",
                "definition": "Share of answers that correctly declined when the documents did not cover the question.",
                "why": "A grounded system must be able to say 'I don't know'.",
            },
        ],
        "caveat": (
            "Answer relevance is reported as a lexical-overlap proxy. It penalises "
            "correct paraphrases and must not be presented as a human quality judgement."
        ),
    }
