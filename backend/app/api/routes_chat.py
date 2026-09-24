"""
Chat routes - asking questions and reading conversations.

WHAT GETS PERSISTED, AND WHY
----------------------------
Every assistant turn stores more than the answer text:

  * the provider and model that ACTUALLY served it, plus whether a fallback was used
  * the grounding verdict and the per-check detail
  * the retrieved chunks with their real scores
  * the resolved citations
  * one TraceEvent row per pipeline stage, with real durations

That is what makes RAG Trace work after a page reload, and what lets the Analytics
page report real numbers instead of plausible ones. If we only stored the answer
text, every downstream claim about "how it was produced" would be unverifiable.

THE CANONICAL ANSWER (spec section 60)
--------------------------------------
`Message.content` is the canonical grounded answer. Translation and shortening
create `AnswerVariant` rows. Nothing in this file ever overwrites `content` with a
transformed version.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from fastapi import APIRouter, Query, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.errors import CarinaaError, NotFound
from app.core.logging import get_logger
from app.db.models import (
    AnswerVariant,
    Conversation,
    ConversationDocument,
    Chunk,
    Document,
    Message,
    QueryLog,
    TraceEvent,
    User,
    Workspace,
    utcnow,
)
from app.rag.pipeline import RAGAnswer, get_rag_pipeline, retrieve_only
from app.rag.export import build_html, build_markdown
from app.rag.sections import detect_relative_section, match_section
from app.rag.units import match_unit_section
from app.rag.references import (
    needs_document_clarification,
    resolve_relative_page,
)
from app.rag.understanding import detect_page_reference
from app.rag.trace import TraceRecorder, stage_definitions
from app.schemas.chat import (
    AskRequest,
    AskResponse,
    CitationOut,
    ConversationCreate,
    ConversationListOut,
    ConversationOut,
    ConversationUpdate,
    GroundingOut,
    MessageOut,
    RetrieveRequest,
    RetrieveResponse,
    TraceOut,
)
from app.websearch import is_allowed as web_allowed
from app.websearch import search as web_search

logger = get_logger(__name__)
router = APIRouter(tags=["chat"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_workspace(db: DbSession, user: CurrentUser, workspace_id: int) -> Workspace:
    workspace = db.scalar(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.user_id == user.id)
    )
    if workspace is None:
        raise NotFound("That workspace does not exist.")
    return workspace


def _require_conversation(db: DbSession, user: CurrentUser, conversation_id: int) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user.id
        )
    )
    if conversation is None:
        raise NotFound("That conversation does not exist.")
    return conversation


def _conversation_scope(db: DbSession, conversation_id: int) -> list[int]:
    """Document ids actively attached to this conversation.

    Empty list means "no chat scope", which is NOT the same as "search nothing" -
    it means fall back to the whole workspace. That fallback is deliberate: a chat
    with no attachments must keep working as a general knowledge-base chatbot, so
    the user is never forced to attach a file before asking anything.
    """
    return list(
        db.scalars(
            select(ConversationDocument.document_id).where(
                ConversationDocument.conversation_id == conversation_id,
                ConversationDocument.is_active.is_(True),
            )
        ).all()
    )


def _resolve_retrieval_scope(
    db: DbSession, conversation_id: int, explicit_ids: list[int] | None
) -> list[int] | None:
    """Decide what retrieval is allowed to search.

    An explicit per-message selection wins, so a single question can be narrowed
    without disturbing the conversation. Otherwise the conversation's active
    attachments apply. `None` means the whole workspace.

    This is resolved on the SERVER, not passed in from the client, so the scope
    cannot be widened by a modified request - the filter is applied before vector
    search and an out-of-scope chunk never reaches the context.
    """
    if explicit_ids:
        return explicit_ids
    scope = _conversation_scope(db, conversation_id)
    return scope or None


# How many previous turns to give the model. Six is three exchanges - enough to
# resolve "what is my name?" and "the second one", without pushing the retrieved
# excerpts so far from the question that a small model starts answering from the
# conversation instead of the documents.
_HISTORY_TURNS = 6


def _load_history(db: DbSession, conversation_id: int, limit: int = _HISTORY_TURNS) -> list[dict[str, str]]:
    """The most recent turns of this conversation, oldest first.

    Only user and assistant text. The assistant's answer is included because a
    follow-up often refers to what Carinaa just said ("explain the second one"),
    not only to what the user said.
    """
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit)
    ).all()
    return [
        {"role": row.role, "content": row.content or ""}
        for row in reversed(rows)
        if row.role in ("user", "assistant") and (row.content or "").strip()
    ]


def _page_range(db: DbSession, scope: list[int] | None, workspace_id: int) -> tuple[int, int] | None:
    """(min, max) page number available in scope, or None when there is no page data.

    Used to answer "page 20 does not exist" honestly. Only reported when real
    metadata confirms it - guessing a page count would be worse than saying nothing.
    """
    # `page_end` for the upper bound, not `page_number`. A short document becomes one
    # chunk covering several pages, so its `page_number` is 1 while its `page_end` is
    # the real last page. Using `page_number` for both bounds reported "pages 1 to 1"
    # for a four-page document and rejected every page question.
    query = select(
        func.min(Chunk.doc_metadata["page_number"]),
        func.max(Chunk.doc_metadata["page_end"]),
    )
    query = query.where(Chunk.workspace_id == workspace_id)
    if scope:
        query = query.where(Chunk.document_id.in_(list(scope)))
    try:
        lowest, highest = db.execute(query).one()
    except Exception:  # noqa: BLE001 - metadata shape varies by file type
        return None
    if lowest is None or highest is None:
        return None
    return int(lowest), int(highest)


def _scope_filenames(db: DbSession, scope: list[int] | None, workspace_id: int) -> list[str]:
    """Filenames of the documents retrieval is allowed to search.

    Used to decide whether a page question is ambiguous: "page 2" means nothing
    across two documents, so with more than one in scope and none named, the honest
    response is to ask which - not to pick one and answer confidently from it.
    """
    query = select(Document.original_filename).where(
        Document.workspace_id == workspace_id, Document.status == "ready"
    )
    if scope:
        query = query.where(Document.id.in_(list(scope)))
    return [name for (name,) in db.execute(query).all() if name]


def _scope_sections(db: DbSession, scope: list[int] | None, workspace_id: int) -> list[str]:
    """Distinct section titles in the documents retrieval may search.

    Section matching needs the REAL titles, because a section is a name that exists
    only in the document - unlike a page, which is a number detectable from the
    question alone. Reading them from the indexed metadata means the feature works on
    whatever the documents actually contain, rather than on titles someone guessed at.
    """
    query = select(Chunk.doc_metadata["section"]).where(Chunk.workspace_id == workspace_id)
    if scope:
        query = query.where(Chunk.document_id.in_(list(scope)))
    try:
        rows = db.execute(query.distinct()).all()
    except Exception:  # noqa: BLE001 - metadata shape varies by file type
        return []
    return sorted({str(value) for (value,) in rows if value})


def _with_scope(
    retrieval: dict[str, Any], scope: list[int] | None, workspace_ready: int
) -> dict[str, Any]:
    """Attach the resolved retrieval scope to a message's retrieval payload.

    Stored rather than recomputed on read, because the scope can change after the
    answer was given. Recomputing would show today's scope next to yesterday's
    answer, which is exactly the kind of quiet inconsistency this project exists to
    avoid: the reader would believe the answer searched documents it never saw.
    """
    retrieval["retrieval_scope"] = "workspace" if scope is None else "chat"
    retrieval["scope_document_ids"] = list(scope) if scope else []
    retrieval["workspace_documents_available"] = workspace_ready
    retrieval["workspace_documents_searched"] = (
        workspace_ready if scope is None else len(scope)
    )
    return retrieval


def _require_message(db: DbSession, user: CurrentUser, message_id: int) -> Message:
    message = db.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.id == message_id, Conversation.user_id == user.id)
    )
    if message is None:
        raise NotFound("That message does not exist.")
    return message


def _question_for(db: DbSession, message: Message) -> str:
    """The user turn that this assistant answer belongs to.

    Learning Mode is per-message: the panel must show the question that produced the
    answer being inspected, and everything below it must belong to that same turn.
    An assistant row stores no question, so it is resolved here - the nearest user
    message at or before this one, in the same conversation.
    """
    previous = db.scalars(
        select(Message)
        .where(
            Message.conversation_id == message.conversation_id,
            Message.role == "user",
            Message.id <= message.id,
        )
        .order_by(Message.id.desc())
        .limit(1)
    ).first()
    return (previous.content or "") if previous else ""


def _persist_trace(
    db: DbSession, *, message_id: int, trace: TraceRecorder
) -> None:
    """Write one TraceEvent row per stage against a message.

    `created_at` is passed explicitly rather than left to the column default. The
    default fires at INSERT time, which is AFTER the work finished, so without this
    every event would be stamped with the same second - making the timestamped trace
    useless and, worse, misleading: it would imply the whole pipeline ran
    instantaneously.
    """
    for event in trace.events:
        db.add(
            TraceEvent(
                message_id=message_id,
                trace_id=trace.trace_id,
                seq=event.seq,
                stage=event.stage,
                status=event.status,
                duration_ms=event.duration_ms,
                event_data={**event.data, "label": event.label},
                created_at=event.created_at or utcnow(),
            )
        )
    db.commit()


def _resolve_mode(user: CurrentUser, requested: str | None) -> str:
    """Decide the AI mode. Server-side default wins over a missing request value."""
    if requested in ("online", "offline"):
        return requested
    preference = (user.preferences or {}).get("default_ai_mode")
    if preference in ("online", "offline"):
        return preference
    return settings.default_ai_mode


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
@router.get("/conversations", response_model=ConversationListOut)
def list_conversations(
    user: CurrentUser,
    db: DbSession,
    workspace_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> ConversationListOut:
    statement = select(Conversation).where(Conversation.user_id == user.id)
    if workspace_id is not None:
        statement = statement.where(Conversation.workspace_id == workspace_id)
    statement = statement.order_by(Conversation.updated_at.desc()).limit(limit)

    rows = db.scalars(statement).all()

    counts = dict(
        db.execute(
            select(Message.conversation_id, func.count())
            .where(Message.conversation_id.in_([r.id for r in rows] or [0]))
            .group_by(Message.conversation_id)
        ).all()
    )

    out = []
    for row in rows:
        item = ConversationOut.model_validate(row)
        item.message_count = int(counts.get(row.id, 0))
        out.append(item)

    return ConversationListOut(conversations=out, total=len(out))


@router.post("/conversations", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate, user: CurrentUser, db: DbSession
) -> ConversationOut:
    _require_workspace(db, user, payload.workspace_id)

    conversation = Conversation(
        workspace_id=payload.workspace_id,
        user_id=user.id,
        title=payload.title.strip() or "New conversation",
        ai_mode=payload.ai_mode,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int, user: CurrentUser, db: DbSession
) -> dict:
    """A conversation with all of its messages."""
    conversation = _require_conversation(db, user, conversation_id)

    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    ).all()

    variants = db.scalars(
        select(AnswerVariant).where(
            AnswerVariant.message_id.in_([m.id for m in messages] or [0])
        )
    ).all()

    variants_by_message: dict[int, list[dict]] = {}
    for variant in variants:
        variants_by_message.setdefault(variant.message_id, []).append(
            {
                "kind": variant.kind,
                "language": variant.language,
                "level": variant.level,
                "content": variant.content,
                "citations": variant.citations or [],
                "validation": variant.validation or {},
                "provider": variant.provider,
                "model": variant.model,
            }
        )

    return {
        "conversation": ConversationOut.model_validate(conversation).model_dump(),
        "messages": [
            {
                **MessageOut.model_validate(m).model_dump(),
                "variants": variants_by_message.get(m.id, []),
            }
            for m in messages
        ],
    }


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def update_conversation(
    conversation_id: int, payload: ConversationUpdate, user: CurrentUser, db: DbSession
) -> ConversationOut:
    conversation = _require_conversation(db, user, conversation_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(conversation, key, value)
    db.commit()
    db.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, user: CurrentUser, db: DbSession) -> None:
    conversation = _require_conversation(db, user, conversation_id)
    db.delete(conversation)
    db.commit()


# ---------------------------------------------------------------------------
# Ask - shared preparation
# ---------------------------------------------------------------------------
# `/chat/ask` and `/chat/ask/stream` must never disagree about what happened for a
# question: the same validation, the same conversation handling, the same web
# search, the same scope, the same page/section resolution, the same persistence,
# the same response payload. The only thing that differs is whether the model's
# output is buffered or streamed.
#
# So everything except the "buffer a whole generation" step lives in shared
# helpers below. Both endpoints call them, which is what makes the agreement a
# structural fact rather than a convention.
@dataclass
class _AskContext:
    """Everything the pipeline and the persistence layer need, resolved once."""

    workspace: Workspace
    conversation: Conversation
    user_message: Message
    mode: str
    question: str
    trace: TraceRecorder
    web_sources: list[dict]
    resolved_scope: list[int] | None
    workspace_ready: int
    scope_names: list[str]
    requested_page: int | None
    page_phrase: str
    relative_note: str
    section_match: str | None
    section_is_relative: str | None
    conversation_history: list[dict[str, str]]


def _prepare_ask(
    db: Session, user: User, payload: AskRequest
) -> _AskContext:
    """Validate, create the conversation + user row, search the web if asked,
    resolve the retrieval scope, and detect page/section/unit references.

    Nothing here streams. Raises `CarinaaError` for every honest refusal -
    ambiguous references, pages that do not exist, workspaces the user does not
    own. Because that behaviour is shared with `/chat/ask`, the two endpoints
    cannot drift on what counts as a valid question.
    """
    workspace = _require_workspace(db, user, payload.workspace_id)
    mode = _resolve_mode(user, payload.mode)

    # ---- conversation ----------------------------------------------------
    if payload.conversation_id is not None:
        conversation = _require_conversation(db, user, payload.conversation_id)
        if conversation.workspace_id != workspace.id:
            raise CarinaaError(
                "That conversation belongs to a different workspace.",
                code="conversation_workspace_mismatch",
                status_code=status.HTTP_409_CONFLICT,
            )
    else:
        conversation = Conversation(
            workspace_id=workspace.id,
            user_id=user.id,
            title=payload.question.strip()[:80] or "New conversation",
            ai_mode=mode,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

    # ---- persist the user's turn immediately ------------------------------
    # Committing before generation means the question is never lost if the
    # provider fails - the user can retry and see what they asked.
    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=payload.question.strip(),
        ai_mode=mode,
    )
    db.add(user_message)
    if conversation.title in ("", "New conversation"):
        conversation.title = payload.question.strip()[:80]
    conversation.ai_mode = mode
    db.commit()

    # ---- optional web search ---------------------------------------------
    trace = TraceRecorder()
    web_sources: list[dict] = []
    allowed, reason = web_allowed(mode, payload.use_web_search)

    if payload.use_web_search:
        if allowed:
            with trace.stage("web_search") as info:
                outcome = web_search(payload.question)
                web_sources = [r.as_dict() for r in outcome.results]
                info.update(
                    {
                        "query": outcome.query,
                        "results": len(outcome.results),
                        "duration_ms": outcome.duration_ms,
                        "error": outcome.error,
                        "sources_are_separate": True,
                    }
                )
        else:
            trace.skip("web_search", reason)

    # ---- resolve the retrieval scope --------------------------------------
    resolved_scope = _resolve_retrieval_scope(db, conversation.id, payload.document_ids)

    # ---- understand the question ------------------------------------------
    requested_page, page_phrase, page_is_relative, page_direction = detect_page_reference(
        payload.question
    )
    relative_note = ""
    if page_is_relative:
        resolved_page, relative_source = resolve_relative_page(
            page_direction, _load_history(db, conversation.id)
        )
        if resolved_page is not None:
            requested_page = resolved_page
            relative_note = f"Resolved from {relative_source}."
            page_is_relative = False

    scope_names = _scope_filenames(db, resolved_scope, workspace.id)
    ambiguous = needs_document_clarification(payload.question, requested_page, scope_names)
    if ambiguous is not None:
        raise CarinaaError(
            "Which document did you mean? This chat has "
            f"{len(ambiguous)} documents in scope, and a page number alone does not "
            "say which one to look in.",
            code="ambiguous_document",
            status_code=400,
            detail={"candidates": ambiguous, "requested_page": requested_page},
        )

    if requested_page is not None:
        span = _page_range(db, resolved_scope, workspace.id)

        if span is None:
            raise CarinaaError(
                "The documents in scope have no page information, so a page number "
                "does not apply. This happens with plain text, Markdown and CSV "
                "files, which are not paginated. Ask about the content instead.",
                code="no_page_metadata",
                status_code=400,
                detail={"requested_page": requested_page, "page_metadata": False},
            )

        if not (span[0] <= requested_page <= span[1]):
            raise CarinaaError(
                f"The documents in scope have pages {span[0]} to {span[1]}, so "
                f"page {requested_page} does not exist.",
                code="page_out_of_range",
                status_code=400,
                detail={
                    "requested_page": requested_page,
                    "available_pages": {"from": span[0], "to": span[1]},
                },
            )

    available_sections = _scope_sections(db, resolved_scope, workspace.id)
    section_match = match_section(payload.question, available_sections)

    unit_section = None
    if section_match is None:
        unit_section = match_unit_section(payload.question, available_sections)
        if unit_section:
            section_match = unit_section
    section_is_relative = detect_relative_section(payload.question)

    conversation_history = _load_history(db, conversation.id)

    workspace_ready = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.workspace_id == workspace.id,
                Document.status == "ready",
            )
        )
        or 0
    )

    return _AskContext(
        workspace=workspace,
        conversation=conversation,
        user_message=user_message,
        mode=mode,
        question=payload.question.strip(),
        trace=trace,
        web_sources=web_sources,
        resolved_scope=resolved_scope,
        workspace_ready=workspace_ready,
        scope_names=scope_names,
        requested_page=requested_page,
        page_phrase=page_phrase,
        relative_note=relative_note,
        section_match=section_match,
        section_is_relative=section_is_relative,
        conversation_history=conversation_history,
    )


def _annotate_trace(ctx: _AskContext, result: RAGAnswer) -> None:
    """Attach the per-question scope, references, and retrieved-source summary
    to the `candidate_retrieval` trace event the pipeline already recorded.

    An answer is only interpretable next to its scope: "5 excerpts" means
    something different when 2 documents were in scope versus 12.
    """
    trace = ctx.trace
    for event in trace.events:
        if event.stage != "candidate_retrieval":
            continue
        event.data["question"] = ctx.question

        # WHAT WAS ACTUALLY RETRIEVED, in the trace rather than only on the message.
        # The Learning panel builds its per-message diagram from the real evidence.
        retrieved_chunks = (result.retrieval.chunks if result.retrieval else []) or []
        if retrieved_chunks:
            event.data["top_sources"] = [
                {
                    "label": chunk.citation_label(),
                    "document": chunk.document_name,
                    "section": str((chunk.metadata or {}).get("section") or ""),
                    "score": round(float(chunk.score or 0.0), 4),
                    "rank": chunk.rank,
                }
                for chunk in retrieved_chunks[:6]
            ]

        # The hybrid diagram, in compact form. Learning Mode draws the real
        # three-stage structure (dense list + bm25 list -> RRF -> order) from
        # this. Only the top few labels per side are carried here because every
        # trace event row is stored; the full three rankings live on the message
        # row's `retrieval` JSON, which the Retrieval surfaces read.
        hybrid = (result.retrieval.hybrid if result.retrieval else {}) or {}
        if hybrid and hybrid.get("fused"):
            def _side(entries: list[dict], limit: int = 4) -> list[dict]:
                return [
                    {
                        "label": entry.get("label"),
                        "document": entry.get("document_name"),
                        "score": entry.get("score"),
                    }
                    for entry in (entries or [])[:limit]
                ]

            event.data["hybrid"] = {
                "dense_count": hybrid.get("dense_count", 0),
                "bm25_count": hybrid.get("bm25_count", 0),
                "overlap_count": hybrid.get("overlap_count", 0),
                "dense_only": hybrid.get("dense_only", 0),
                "bm25_only": hybrid.get("bm25_only", 0),
                "rrf_k": hybrid.get("rrf_k"),
                "dense": _side(hybrid.get("dense")),
                "bm25": _side(hybrid.get("bm25")),
                "fused": _side(hybrid.get("fused")),
            }
        if ctx.requested_page is not None:
            event.data.update(
                {
                    "page_reference": ctx.page_phrase,
                    "page_filter_applied": ctx.requested_page,
                }
            )
            if ctx.relative_note:
                event.data["page_resolution"] = ctx.relative_note
        if ctx.section_match:
            event.data["section_filter_applied"] = ctx.section_match
        if ctx.section_is_relative:
            event.data["relative_section"] = ctx.section_is_relative
        if ctx.conversation_history:
            event.data["conversation_turns_used"] = len(ctx.conversation_history)

        event.data["scope_file_names"] = (
            list(ctx.scope_names) if ctx.resolved_scope else []
        )

        if ctx.resolved_scope is None:
            event.data.update(
                {
                    "retrieval_scope": "workspace",
                    "scope_document_ids": [],
                    "workspace_documents_available": ctx.workspace_ready,
                    "workspace_documents_searched": ctx.workspace_ready,
                    "scope_note": (
                        "No chat scope was set, so the whole workspace was searched."
                    ),
                }
            )
        else:
            event.data.update(
                {
                    "retrieval_scope": "chat",
                    "scope_document_ids": list(ctx.resolved_scope),
                    "workspace_documents_available": ctx.workspace_ready,
                    "workspace_documents_searched": len(ctx.resolved_scope),
                    "scope_note": (
                        f"Retrieval was limited to {len(ctx.resolved_scope)} selected "
                        f"document(s) out of {ctx.workspace_ready} in the workspace."
                    ),
                }
            )


def _persist_assistant_turn(
    db: Session, ctx: _AskContext, result: RAGAnswer
) -> tuple[Message, AskResponse]:
    """Persist the answer, its trace and its analytics row. Returns the loaded
    Message (with a real id) and the assembled AskResponse.

    This is the ONLY place an assistant Message is written, so the two endpoints
    cannot drift on any of the columns, the QueryLog shape, or the response
    envelope.
    """
    _annotate_trace(ctx, result)

    assistant_message = Message(
        conversation_id=ctx.conversation.id,
        role="assistant",
        content=result.answer,
        ai_mode=ctx.mode,
        provider=result.provider,
        model=result.model,
        used_fallback=result.used_fallback,
        fallback_reason=result.fallback_reason,
        grounding_status=result.grounding.status if result.grounding else "",
        grounding_detail=result.grounding.as_dict() if result.grounding else {},
        latency_ms=result.total_ms,
        token_usage=result.token_usage,
        trace_id=ctx.trace.trace_id,
        retrieval=_with_scope(
            result.retrieval.as_dict() if result.retrieval else {},
            ctx.resolved_scope,
            ctx.workspace_ready,
        ),
        citations=result.citation_dicts,
        web_search_used=bool(ctx.web_sources),
        web_sources=ctx.web_sources,
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)

    _persist_trace(db, message_id=assistant_message.id, trace=ctx.trace)

    retrieval = result.retrieval
    db.add(
        QueryLog(
            workspace_id=ctx.workspace.id,
            user_id=ctx.conversation.user_id,
            message_id=assistant_message.id,
            ai_mode=ctx.mode,
            provider=result.provider,
            model=result.model,
            retrieved_count=retrieval.candidates_retrieved if retrieval else 0,
            used_count=len(retrieval.chunks) if retrieval else 0,
            top_score=retrieval.top_score if retrieval else 0.0,
            mean_score=retrieval.mean_score if retrieval else 0.0,
            retrieval_ms=retrieval.total_ms if retrieval else 0,
            generation_ms=result.generation_ms,
            total_ms=result.total_ms,
            grounding_status=result.grounding.status if result.grounding else "",
            citation_count=len(result.citations.citations) if result.citations else 0,
            refused=bool(result.grounding and result.grounding.refused),
        )
    )

    ctx.conversation.updated_at = utcnow()
    db.commit()

    grounding_out = (
        GroundingOut(
            **{
                k: v
                for k, v in result.grounding.as_dict().items()
                if k in GroundingOut.model_fields
            }
        )
        if result.grounding
        else None
    )
    citation_dicts = result.citation_dicts
    citations_out = [
        CitationOut(**{k: v for k, v in c.items() if k in CitationOut.model_fields})
        for c in citation_dicts
    ]
    response = AskResponse(
        message=MessageOut.model_validate(assistant_message),
        conversation_id=ctx.conversation.id,
        answer=result.answer,
        provider_label=result.provider_label(),
        is_extractive_failsafe=result.is_extractive_failsafe,
        grounding=grounding_out,
        citations=citations_out,
        retrieval=result.retrieval.as_dict() if result.retrieval else {},
        context=result.context.as_dict() if result.context else {},
        trace=ctx.trace.summary(),
        web_sources=ctx.web_sources,
    )
    return assistant_message, response


# ---------------------------------------------------------------------------
# Ask (buffered, non-streaming)
# ---------------------------------------------------------------------------
@router.post("/chat/ask", response_model=AskResponse)
async def ask(payload: AskRequest, user: CurrentUser, db: DbSession) -> AskResponse:
    """Ask a question and get a grounded, cited answer.

    This is the one-shot variant of the same path `/chat/ask/stream` exposes
    incrementally — same preparation, same pipeline, same persistence, same
    response envelope. Only the timing differs.
    """
    ctx = _prepare_ask(db, user, payload)
    pipeline = get_rag_pipeline()
    try:
        result = await pipeline.answer(
            db,
            workspace_id=ctx.workspace.id,
            question=ctx.question,
            mode=ctx.mode,
            top_k=payload.top_k,
            candidate_k=payload.candidate_k,
            document_ids=ctx.resolved_scope,
            use_rerank=payload.use_rerank,
            web_sources=ctx.web_sources or None,
            language=payload.language,
            trace=ctx.trace,
            history=ctx.conversation_history,
            page_number=ctx.requested_page,
            section=ctx.section_match,
        )
    except CarinaaError as exc:
        _persist_failure_trace(db, ctx, exc)
        raise _annotated_error(exc, ctx) from exc

    _, response = _persist_assistant_turn(db, ctx, result)
    return response


def _persist_failure_trace(
    db: Session, ctx: _AskContext, exc: CarinaaError
) -> None:
    """Persist the events recorded up to the failed stage against the USER row.

    A FAILED RUN STILL HAS A TRACE, AND IT IS WORTH KEEPING.
    The panel's job is to say WHICH stage failed. If a failed run's events were
    discarded, the panel would fall back to the previous successful run and show
    a fully completed pipeline next to an error — exactly the dishonesty this
    project exists to avoid.
    """
    with contextlib.suppress(Exception):
        _persist_trace(db, message_id=ctx.user_message.id, trace=ctx.trace)


def _annotated_error(exc: CarinaaError, ctx: _AskContext) -> CarinaaError:
    """Wrap a pipeline failure with the question, message id and trace summary,
    so the client can render the failure honestly.
    """
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    return CarinaaError(
        exc.message,
        code=exc.code,
        status_code=exc.status_code,
        detail={
            **detail,
            "failed": True,
            "question": ctx.question,
            "message_id": ctx.user_message.id,
            "trace": ctx.trace.summary(),
        },
    )


# ---------------------------------------------------------------------------
# Ask (real token-by-token streaming over Server-Sent Events)
# ---------------------------------------------------------------------------
@router.post("/chat/ask/stream")
async def ask_stream(
    payload: AskRequest, user: CurrentUser, db: DbSession
):
    """Stream a grounded, cited answer as Server-Sent Events.

    The events:
        ready   - emitted first; conversation + user_message ids so the client can
                  align the streamed answer with the optimistic row it rendered.
        stage   - mirrors a TraceRecorder `add` call, fired the moment a real
                  pipeline stage finishes. Never scheduled on a timer - the
                  events ARE the schedule.
        token   - a real provider fragment, in order.
        done    - full AskResponse payload for the completed answer.
        error   - the failure envelope (`code`, `message`, `detail`, `trace`)
                  that `/chat/ask` would have returned as HTTP 4xx/5xx, wrapped
                  as a normal SSE frame so the client can render a partial-run
                  failure the same way as a whole-run failure.

    The client MUST render the streamed tokens as an *uncommitted draft* until
    `done` arrives. If a mid-stream failure occurs, only the question row is
    persisted and the draft is discarded. That is what keeps the canonical
    answer a single canonical fact: whatever the browser showed during the
    stream, the persisted state is the answer.
    """
    # Everything that raises a normal HTTP error (workspace not found, page
    # out of range, ambiguous document, ...) still raises BEFORE the SSE
    # headers are written. Once we return a StreamingResponse we are on the
    # stream, and errors have to be reported as `error` SSE frames instead.
    ctx = _prepare_ask(db, user, payload)

    pipeline = get_rag_pipeline()
    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()

    def _emit(item: dict[str, Any]) -> None:
        """Push one event onto the stream queue from any thread.

        Two different threads legitimately produce events here:
          - the retriever runs on a worker thread (pipeline._prepare awaits
            asyncio.to_thread), so its trace events arrive off-loop; and
          - generation-stage events (llm_generation, citation_resolution,
            grounding) and the token deltas arrive on the loop thread itself.

        `call_soon_threadsafe` is required for the off-loop case but WRONG for
        the on-loop case: it defers the push to the next loop iteration, so a
        fast synchronous producer (all the stages, then all the tokens, before
        the consumer ever suspends) queues every token directly while the
        stage callbacks sit unscheduled - and the consumer reaches the terminal
        frame and stops before they ever run. Detection is therefore explicit:
        on the loop thread, push now (correct FIFO, stages precede tokens); off
        it, hop threads safely. This is why a streamed answer shows its pipeline
        lighting up in real time rather than only ever showing the tokens.
        """
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            events.put_nowait(item)
        else:
            loop.call_soon_threadsafe(events.put_nowait, item)

    def _push_stage(event: Any) -> None:
        _emit(
            {
                "type": "stage",
                "seq": event.seq,
                "stage": event.stage,
                "label": event.label,
                "status": event.status,
                "duration_ms": event.duration_ms,
                "data": event.data,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
        )

    def _push_pipeline(kind: str, **payload: Any) -> None:
        _emit({"type": kind, **payload})


    ctx.trace.subscribe(_push_stage)

    async def _drain() -> None:
        """Consume `pipeline.stream_answer` and forward its events to the queue.

        Errors are converted to the same shape the buffered endpoint raises, so
        the client's error renderer stays single-source.
        """
        try:
            async for event in pipeline.stream_answer(
                db,
                workspace_id=ctx.workspace.id,
                question=ctx.question,
                mode=ctx.mode,
                top_k=payload.top_k,
                candidate_k=payload.candidate_k,
                document_ids=ctx.resolved_scope,
                use_rerank=payload.use_rerank,
                web_sources=ctx.web_sources or None,
                language=payload.language,
                trace=ctx.trace,
                history=ctx.conversation_history,
                page_number=ctx.requested_page,
                section=ctx.section_match,
            ):
                if event["type"] == "delta":
                    _push_pipeline("token", text=event["text"])
                elif event["type"] == "done":
                    _push_pipeline("final_result", result=event["result"])
                    break
        except CarinaaError as exc:
            _persist_failure_trace(db, ctx, exc)
            _push_pipeline("stream_error", error=_annotated_error(exc, ctx))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Streaming ask failed unexpectedly")
            _push_pipeline(
                "stream_error",
                error=CarinaaError(
                    str(exc), code="internal_error", status_code=500
                ),
            )

    async def _generator() -> AsyncIterator[str]:
        yield _sse_frame("ready", {
            "conversation_id": ctx.conversation.id,
            "user_message_id": ctx.user_message.id,
            "trace_id": ctx.trace.trace_id,
            "mode": ctx.mode,
        })

        drain_task = asyncio.create_task(_drain())
        try:
            while True:
                try:
                    item = await events.get()
                except asyncio.CancelledError:
                    raise
                kind = item.get("type")
                if kind == "stage":
                    yield _sse_frame("stage", item)
                elif kind == "token":
                    yield _sse_frame("token", {"text": item["text"]})
                elif kind == "final_result":
                    try:
                        _, response = _persist_assistant_turn(
                            db, ctx, item["result"]
                        )
                    except Exception as exc:  # noqa: BLE001 - persistence is best-effort post-stream
                        logger.exception("Persisting streamed answer failed")
                        yield _sse_frame("error", {
                            "code": "persistence_error",
                            "message": "The answer was produced but could not be saved.",
                            "detail": {"reason": exc.__class__.__name__},
                        })
                        break
                    yield _sse_frame("done", json.loads(response.model_dump_json()))
                    break
                elif kind == "stream_error":
                    err: CarinaaError = item["error"]
                    yield _sse_frame("error", {
                        "code": err.code,
                        "message": err.message,
                        "detail": err.detail or {},
                    })
                    break
        finally:
            if not drain_task.done():
                drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await drain_task

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse_frame(event: str, data: Any) -> str:
    """One SSE frame. Multi-line data payloads are joined with `\\n` inside
    consecutive `data:` lines, as the spec requires; a JSON body never has raw
    newlines here, but the format is exact anyway so this stays correct for
    future event shapes.
    """
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


# ---------------------------------------------------------------------------
# Retrieval-only (Playground)
# ---------------------------------------------------------------------------
@router.post("/chat/retrieve", response_model=RetrieveResponse)
async def retrieve(payload: RetrieveRequest, user: CurrentUser, db: DbSession) -> RetrieveResponse:
    """Run retrieval and context building WITHOUT calling a language model."""
    workspace = _require_workspace(db, user, payload.workspace_id)

    # Resolve the chat scope exactly as /chat/ask does, so retrieval-only and a
    # real answer can never disagree about what was searched.
    scope: list[int] | None = payload.document_ids
    if scope is None and payload.conversation_id is not None:
        conversation = _require_conversation(db, user, payload.conversation_id)
        if conversation.workspace_id != workspace.id:
            raise NotFound("That conversation is not in this workspace.")
        scope = _resolve_retrieval_scope(db, conversation.id, None)

    # The same structural filters /chat/ask applies, so the labs demonstrate the real
    # behaviour rather than an approximation of it.
    requested_page = payload.page_number
    if requested_page is None:
        detected, _phrase, _relative, _direction = detect_page_reference(payload.question)
        requested_page = detected

    # Mirror the ask endpoint's unit handling. Without this the labs would show a
    # different search from what a real answer performs - the exact disagreement the
    # brief warns about.
    section_filter = payload.section
    if section_filter is None:
        section_filter = match_unit_section(
            payload.question, _scope_sections(db, scope, workspace.id)
        )

    outcome = await retrieve_only(
        db,
        workspace_id=workspace.id,
        question=payload.question,
        top_k=payload.top_k,
        candidate_k=payload.candidate_k,
        document_ids=scope,
        page_number=requested_page,
        section=section_filter,
        use_rerank=payload.use_rerank,
        mode=payload.mode,
    )

    if "error" in outcome:
        return RetrieveResponse(
            error=str(outcome.get("error", "")), note=outcome.get("note", "")
        )

    return RetrieveResponse(
        retrieval=outcome.get("retrieval", {}),
        context=outcome.get("context", {}),
        trace=outcome.get("trace", {}),
        generated=False,
        note=str(outcome.get("note", "")),
    )


# ---------------------------------------------------------------------------
# Retrieval Lab: dense vs BM25 vs hybrid, one question, side by side
# ---------------------------------------------------------------------------
class RetrieveCompareRequest(BaseModel):
    workspace_id: int
    question: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    candidate_k: int | None = Field(default=None, ge=1, le=200)
    document_ids: list[int] | None = None
    conversation_id: int | None = None


@router.post("/chat/retrieve/compare")
async def retrieve_compare(
    payload: RetrieveCompareRequest, user: CurrentUser, db: DbSession
) -> dict:
    """Run the SAME retrieval three times - dense, bm25, hybrid - and compare.

    WHY ONE ENDPOINT AND NOT THREE CLIENT CALLS. The comparison is only
    meaningful when all three modes see an identical population: same workspace,
    same chat scope, same question. Resolving that scope once server-side and
    running all three here is what guarantees the three columns of the lab are
    literally comparable. Three client calls could drift if, say, a document was
    re-indexed between them.

    The three runs are concurrent (asyncio.gather over to_thread), which is both
    faster and keeps the comparison tight in time.

    What the response carries, per mode: the ordered result list with each
    chunk's score on that mode's own scale, plus a `movements` table showing
    where each chunk ranked in each mode. The UI animates the reorganisation
    from that table; nothing here guesses at what the animation will show - it
    reports the actual ranks, and a chunk that appears in one mode but not the
    other is listed as such rather than padded to look common.
    """
    workspace = _require_workspace(db, user, payload.workspace_id)

    scope: list[int] | None = payload.document_ids
    if scope is None and payload.conversation_id is not None:
        conversation = _require_conversation(db, user, payload.conversation_id)
        if conversation.workspace_id != workspace.id:
            raise NotFound("That conversation is not in this workspace.")
        scope = _resolve_retrieval_scope(db, conversation.id, None)

    requested_page = detect_page_reference(payload.question)[0]
    section_filter = match_unit_section(
        payload.question, _scope_sections(db, scope, workspace.id)
    )

    modes = ("dense", "bm25", "hybrid")
    # SEQUENTIAL, deliberately. Each retrieve_only call hands the SAME
    # request-scoped Session to `asyncio.to_thread`, and a SQLAlchemy Session is
    # not safe for concurrent use from several worker threads. A gather here
    # would race three retrievals over one connection - faster on paper, and
    # exactly the kind of subtle, intermittent corruption this project refuses.
    # A lab is measured in a second or two, not in milliseconds.
    outcomes = [
        await retrieve_only(
            db,
            workspace_id=workspace.id,
            question=payload.question,
            top_k=payload.top_k,
            candidate_k=payload.candidate_k,
            document_ids=scope,
            page_number=requested_page,
            section=section_filter,
            use_rerank=False,  # the lab compares RETRIEVERS, not re-rankers
            mode=mode,
        )
        for mode in modes
    ]

    columns: dict[str, Any] = {}
    # A chunk's rank in each mode, keyed by vector_id. A chunk missing from a
    # mode is None there - never -1 or a padded zero, which would imply it was
    # ranked last when it simply was not retrieved.
    rank_matrix: dict[str, dict[str, int | None]] = {}
    label_by_key: dict[str, dict[str, Any]] = {}

    for mode, outcome in zip(modes, outcomes):
        if "error" in outcome:
            columns[mode] = {"error": outcome["error"]}
            continue
        retrieval = outcome.get("retrieval", {}) or {}
        chunks = retrieval.get("chunks", [])
        columns[mode] = {
            "mode": retrieval.get("mode", mode),
            "score_scale": retrieval.get("score_scale", ""),
            "search_ms": retrieval.get("search_ms", 0),
            "results": [
                {
                    "vector_id": c.get("vector_id"),
                    "chunk_id": c.get("chunk_id"),
                    "label": c.get("label"),
                    "document_name": c.get("document_name"),
                    "metadata": c.get("metadata") or {},
                    "rank": c.get("rank"),
                    "score": c.get("score"),
                    "rrf_score": c.get("rrf_score"),
                    "bm25_score": c.get("bm25_score"),
                    "preview": c.get("preview"),
                }
                for c in chunks
            ],
        }
        for c in columns[mode]["results"]:
            key = c.get("vector_id") or f"chunk:{c.get('chunk_id')}"
            rank_matrix.setdefault(key, {m: None for m in modes})[mode] = c["rank"]
            label_by_key.setdefault(key, c)

    # The comparison table: one row per chunk any mode found. Sorted by the
    # smallest rank it achieved anywhere (so the most contested evidence is at
    # the top), then by name. Each row shows the rank in every mode and which
    # modes agreed. This is the actual data the animation reorders; the UI adds
    # no opinion of its own.
    rows = []
    for key, ranks in rank_matrix.items():
        present_in = [m for m in modes if ranks[m] is not None]
        row = {
            "vector_id": key,
            "label": label_by_key[key].get("label"),
            "document_name": label_by_key[key].get("document_name"),
            "preview": (label_by_key[key].get("preview") or "")[:140],
            "ranks": ranks,
            "agreement": len(present_in),
            "found_by": present_in,
            "best_rank": min(v for v in ranks.values() if v is not None),
        }
        rows.append(row)
    rows.sort(key=lambda r: (r["best_rank"], r["label"] or ""))

    # Headline numbers the lab shows under the animation. All counts, all real:
    # how many chunks the two retrievers agreed on, and the single biggest
    # disagreement (a chunk one mode ranked first that the other did not
    # retrieve at all).
    both = sum(1 for r in rows if r["agreement"] == len(modes))
    only_one = [
        r
        for r in rows
        if r["agreement"] == 1 and r["best_rank"] == 0
    ]

    return {
        "question": payload.question,
        "columns": columns,
        "comparison": rows,
        "stats": {
            "total_unique_chunks": len(rows),
            "found_by_all_three": both,
            "top_disagreements": [
                {
                    "label": r["label"],
                    "document_name": r["document_name"],
                    "found_by": r["found_by"],
                }
                for r in only_one[:6]
            ],
        },
    }

# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------
@router.get("/messages/{message_id}/trace", response_model=TraceOut)
def get_trace(message_id: int, user: CurrentUser, db: DbSession) -> TraceOut:
    """The real trace recorded while answering this message."""
    message = _require_message(db, user, message_id)

    events = db.scalars(
        select(TraceEvent)
        .where(TraceEvent.message_id == message_id)
        .order_by(TraceEvent.seq)
    ).all()

    stages = [
        {
            "seq": event.seq,
            "stage": event.stage,
            "label": (event.event_data or {}).get("label", event.stage.replace("_", " ").title()),
            "status": event.status,
            "duration_ms": event.duration_ms,
            "data": {k: v for k, v in (event.event_data or {}).items() if k != "label"},
            # Real wall-clock time from the stored event, so the trace can be read as a
            # log. Older rows predate this field and will simply have none.
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        for event in events
    ]

    return TraceOut(
        trace_id=message.trace_id,
        message_id=message.id,
        event_count=len(stages),
        total_ms=sum(s["duration_ms"] for s in stages),
        stages=stages,
        # Learning Mode is per-message, so the panel needs the question this answer
        # belongs to. Resolved server-side because an assistant row stores no
        # question, and guessing it on the client would be wrong for any message
        # that is not the latest turn.
        question=_question_for(db, message),
        summary={
            "provider": message.provider,
            "model": message.model,
            "used_fallback": message.used_fallback,
            "ai_mode": message.ai_mode,
            "grounding_status": message.grounding_status,
            "citation_count": len(message.citations or []),
            "latency_ms": message.latency_ms,
            "stage_definitions": stage_definitions(),
        },
    )


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_message(message_id: int, user: CurrentUser, db: DbSession) -> None:
    message = _require_message(db, user, message_id)
    db.delete(message)
    db.commit()


# ---------------------------------------------------------------------------
# Evidence Pack export (#54)
# ---------------------------------------------------------------------------
@router.get("/messages/{message_id}/export")
def export_evidence_pack(
    message_id: int,
    user: CurrentUser,
    db: DbSession,
    format: str = Query(default="md", pattern="^(md|html)$"),
    base_url: str | None = Query(default=None, max_length=300),
) -> Response:
    """Download the answer with everything that produced it, as a pack.

    Everything is read from the STORED message and its recorded trace. The export
    never re-runs retrieval or generation - see app/rag/export.py for why that
    would be dishonest: a regenerated pack would look authoritative and be wrong.

    `html` is print-ready (the browser's Print -> Save as PDF renders it to a real
    PDF); `md` is the same content as Markdown. The route does not claim to emit a
    PDF it has not laid out.
    """
    message = _require_message(db, user, message_id)
    question = _question_for(db, message)

    events = db.scalars(
        select(TraceEvent)
        .where(TraceEvent.message_id == message_id)
        .order_by(TraceEvent.seq)
    ).all()
    trace_events = [
        {
            "seq": event.seq,
            "stage": event.stage,
            "label": (event.event_data or {}).get("label", event.stage.replace("_", " ").title()),
            "status": event.status,
            "duration_ms": event.duration_ms,
        }
        for event in events
    ]

    common = {
        "question": question,
        "answer": message.content,
        "provider": message.provider or "",
        "model": message.model or "",
        "used_fallback": bool(message.used_fallback),
        "fallback_reason": message.fallback_reason or "",
        "is_extractive": (message.model or "") == "extractive",
        "ai_mode": message.ai_mode or "online",
        "grounding": message.grounding_detail or None,
        "citations": message.citations or [],
        "retrieval": message.retrieval or None,
        "web_sources": message.web_sources or [],
        "trace_events": trace_events,
        "created_at": message.created_at,
        "latency_ms": message.latency_ms or 0,
        "base_url": base_url,
    }

    if format == "html":
        html_kwargs = {k: v for k, v in common.items() if k != "fallback_reason"}
        body = build_html(**html_kwargs)
        media_type = "text/html; charset=utf-8"
        extension = "html"
    else:
        body = build_markdown(**common)
        media_type = "text/markdown; charset=utf-8"
        extension = "md"

    filename = f"carinaa-evidence-{message_id}.{extension}"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
