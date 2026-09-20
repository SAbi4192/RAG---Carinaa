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

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

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
    Workspace,
    utcnow,
)
from app.rag.pipeline import get_rag_pipeline, retrieve_only
from app.rag.sections import detect_relative_section, match_section
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
# Ask
# ---------------------------------------------------------------------------
@router.post("/chat/ask", response_model=AskResponse)
async def ask(payload: AskRequest, user: CurrentUser, db: DbSession) -> AskResponse:
    """Ask a question and get a grounded, cited answer."""
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
    # Resolved once and reused, so the scope that is APPLIED and the scope that is
    # REPORTED cannot drift apart.
    resolved_scope = _resolve_retrieval_scope(db, conversation.id, payload.document_ids)

    # ---- understand the question ------------------------------------------
    # A page reference is a STRUCTURAL constraint. Dense retrieval compares meaning,
    # and "the second page" means nothing to it, so the constraint is applied as a
    # filter rather than hoped for in the ranking.
    requested_page, page_phrase, page_is_relative, page_direction = detect_page_reference(
        payload.question
    )

    # A relative reference ("the next page") can only be resolved from what the
    # conversation established. When nothing established a page, it stays
    # unresolved rather than being guessed - and the question is answered as a
    # normal question instead.
    relative_note = ""
    if page_is_relative:
        resolved_page, relative_source = resolve_relative_page(
            page_direction, _load_history(db, conversation.id)
        )
        if resolved_page is not None:
            requested_page = resolved_page
            relative_note = f"Resolved from {relative_source}."
            page_is_relative = False

    # Several documents in scope and none named makes "page 2" unanswerable.
    # Asking is better than picking one, because a confident answer from the wrong
    # document is worse than a clarifying question.
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
            # No page metadata anywhere in scope. That is different from a page
            # being out of range: the document has no pages at all. Reporting
            # "could not find it" here would imply the content is missing, when
            # actually the question does not apply to this file type.
            raise CarinaaError(
                "The documents in scope have no page information, so a page number "
                "does not apply. This happens with plain text, Markdown and CSV "
                "files, which are not paginated. Ask about the content instead.",
                code="no_page_metadata",
                status_code=400,
                detail={"requested_page": requested_page, "page_metadata": False},
            )

        if not (span[0] <= requested_page <= span[1]):
            # Confirmed by real metadata, so say so plainly instead of running a
            # search that cannot succeed and letting the model invent something.
            # 400 with a precise message: the page range is real metadata, so this
            # is a confident statement rather than a guess.
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

    # A section is a NAME, so matching runs against the titles actually indexed rather
    # than being parsed out of the question. A question that names no real section
    # falls through to ordinary retrieval.
    section_match = match_section(payload.question, _scope_sections(db, resolved_scope, workspace.id))
    section_is_relative = detect_relative_section(payload.question)

    conversation_history = _load_history(db, conversation.id)

    # ---- run the pipeline -------------------------------------------------
    pipeline = get_rag_pipeline()
    result = await pipeline.answer(
        db,
        workspace_id=workspace.id,
        question=payload.question.strip(),
        mode=mode,
        top_k=payload.top_k,
        candidate_k=payload.candidate_k,
        document_ids=resolved_scope,
        use_rerank=payload.use_rerank,
        web_sources=web_sources or None,
        language=payload.language,
        trace=trace,
        history=conversation_history,
        page_number=requested_page,
        section=section_match,
    )

    # ---- record what was actually searched (brief section 43) -------------
    # An answer is only interpretable next to its scope: "5 excerpts" means
    # something different when 2 documents were in scope versus 12.
    workspace_ready = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.workspace_id == workspace.id,
                Document.status == "ready",
            )
        )
        or 0
    )
    for event in trace.events:
        if event.stage != "candidate_retrieval":
            continue
        if requested_page is not None:
            event.data.update(
                {
                    "page_reference": page_phrase,
                    "page_filter_applied": requested_page,
                }
            )
            if relative_note:
                event.data["page_resolution"] = relative_note
        if section_match:
            event.data["section_filter_applied"] = section_match
        if section_is_relative:
            # Detected but not resolved: the section the conversation is in is not
            # something this layer knows, and guessing one would be worse than
            # answering without the filter.
            event.data["relative_section"] = section_is_relative
        if conversation_history:
            event.data["conversation_turns_used"] = len(conversation_history)

        if resolved_scope is None:
            event.data.update(
                {
                    "retrieval_scope": "workspace",
                    "scope_document_ids": [],
                    "workspace_documents_available": workspace_ready,
                    "workspace_documents_searched": workspace_ready,
                    "scope_note": (
                        "No chat scope was set, so the whole workspace was searched."
                    ),
                }
            )
        else:
            event.data.update(
                {
                    "retrieval_scope": "chat",
                    "scope_document_ids": list(resolved_scope),
                    "workspace_documents_available": workspace_ready,
                    "workspace_documents_searched": len(resolved_scope),
                    "scope_note": (
                        f"Retrieval was limited to {len(resolved_scope)} selected "
                        f"document(s) out of {workspace_ready} in the workspace."
                    ),
                }
            )

    # ---- persist the assistant turn ---------------------------------------
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=result.answer,
        ai_mode=mode,
        provider=result.provider,
        model=result.model,
        used_fallback=result.used_fallback,
        fallback_reason=result.fallback_reason,
        grounding_status=result.grounding.status if result.grounding else "",
        grounding_detail=result.grounding.as_dict() if result.grounding else {},
        latency_ms=result.total_ms,
        token_usage=result.token_usage,
        trace_id=trace.trace_id,
        retrieval=_with_scope(
            result.retrieval.as_dict() if result.retrieval else {},
            resolved_scope,
            workspace_ready,
        ),
        citations=result.citation_dicts,
        web_search_used=bool(web_sources),
        web_sources=web_sources,
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)

    # ---- persist the trace ------------------------------------------------
    #
    # `created_at` is passed explicitly rather than left to the column default.
    # The default fires at INSERT time, which is AFTER the answer has finished, so
    # without this every event would be stamped with the same second - making the
    # timestamped trace useless and, worse, misleading: it would imply the whole
    # pipeline ran instantaneously.
    for event in trace.events:
        db.add(
            TraceEvent(
                message_id=assistant_message.id,
                trace_id=trace.trace_id,
                seq=event.seq,
                stage=event.stage,
                status=event.status,
                duration_ms=event.duration_ms,
                event_data={**event.data, "label": event.label},
                created_at=event.created_at or utcnow(),
            )
        )

    # ---- analytics row ----------------------------------------------------
    retrieval = result.retrieval
    db.add(
        QueryLog(
            workspace_id=workspace.id,
            user_id=user.id,
            message_id=assistant_message.id,
            ai_mode=mode,
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

    conversation.updated_at = utcnow()
    db.commit()

    # ---- respond ----------------------------------------------------------
    grounding_out = (
        GroundingOut(**{k: v for k, v in result.grounding.as_dict().items() if k in GroundingOut.model_fields})
        if result.grounding
        else None
    )

    citation_dicts = result.citation_dicts
    citations_out = [
        CitationOut(**{k: v for k, v in c.items() if k in CitationOut.model_fields})
        for c in citation_dicts
    ]

    return AskResponse(
        message=MessageOut.model_validate(assistant_message),
        conversation_id=conversation.id,
        answer=result.answer,
        provider_label=result.provider_label(),
        is_extractive_failsafe=result.is_extractive_failsafe,
        grounding=grounding_out,
        citations=citations_out,
        retrieval=result.retrieval.as_dict() if result.retrieval else {},
        context=result.context.as_dict() if result.context else {},
        trace=trace.summary(),
        web_sources=web_sources,
    )


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

    outcome = await retrieve_only(
        db,
        workspace_id=workspace.id,
        question=payload.question,
        top_k=payload.top_k,
        candidate_k=payload.candidate_k,
        document_ids=scope,
        page_number=requested_page,
        section=payload.section,
        use_rerank=payload.use_rerank,
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
