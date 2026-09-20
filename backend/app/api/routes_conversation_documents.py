"""Chat-scoped documents.

A conversation can attach documents so retrieval is narrowed to them. This is the
"answer only from PDF 3" use case, and it is enforced at RETRIEVAL time - the
retriever filters by document id before vector search - rather than being merely
requested in the prompt. A prompt-level instruction can be ignored by a model; a
pre-search filter cannot.

Attaching is a REFERENCE, never a copy. The document, its chunks and its vectors
exist once in the workspace; this module only records the relationship. See the
note on `ConversationDocument` in `app.db.models`.

TWO SCOPES, BOTH SUPPORTED
--------------------------
    chat scope       documents actively attached to this conversation
    workspace scope  everything in the workspace (the fallback)

An empty chat scope means workspace scope, not "search nothing" - so a chat with
no attachments keeps working as a general knowledge-base assistant and the user is
never forced to attach a file before asking a question.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFound
from app.db.models import Conversation, ConversationDocument, Document

router = APIRouter(tags=["conversation documents"])


class ConversationDocumentAttach(BaseModel):
    document_id: int


class ConversationDocumentToggle(BaseModel):
    is_active: bool


def _require_conversation(db: DbSession, user: CurrentUser, conversation_id: int) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user.id
        )
    )
    if conversation is None:
        raise NotFound("That conversation does not exist.")
    return conversation


def scope_payload(db: DbSession, conversation: Conversation) -> dict[str, Any]:
    """Attached documents plus what the scope means for retrieval.

    Shared with the chat route so the scope the UI shows and the scope retrieval
    applies can never disagree - they are computed from the same query.
    """
    links = db.scalars(
        select(ConversationDocument)
        .where(ConversationDocument.conversation_id == conversation.id)
        .order_by(ConversationDocument.added_at)
    ).all()

    documents: list[dict[str, Any]] = []
    for link in links:
        document = db.get(Document, link.document_id)
        if document is None:
            continue  # deleted from the workspace; the cascade removes the link
        documents.append(
            {
                "document_id": document.id,
                "original_filename": document.original_filename,
                "file_type": document.file_type,
                "status": document.status,
                "chunk_count": document.chunk_count,
                "is_active": link.is_active,
                "added_at": link.added_at.isoformat() if link.added_at else None,
            }
        )

    active = [item["document_id"] for item in documents if item["is_active"]]

    # The workspace total, so the UI can say "2 of 12" instead of leaving the
    # reader to guess how much was excluded.
    workspace_total = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.workspace_id == conversation.workspace_id,
                Document.status == "ready",
            )
        )
        or 0
    )

    return {
        "conversation_id": conversation.id,
        "documents": documents,
        "active_document_ids": active,
        "scope": "chat" if active else "workspace",
        "workspace_document_count": workspace_total,
        "note": (
            "Retrieval is limited to the active documents in this chat."
            if active
            else "No documents are scoped to this chat, so retrieval searches the whole workspace."
        ),
    }


@router.get("/conversations/{conversation_id}/documents")
def list_conversation_documents(
    conversation_id: int, user: CurrentUser, db: DbSession
) -> dict[str, Any]:
    """The chat's knowledge scope: attached documents and which are active."""
    return scope_payload(db, _require_conversation(db, user, conversation_id))


@router.post("/conversations/{conversation_id}/documents", status_code=status.HTTP_201_CREATED)
def attach_conversation_document(
    conversation_id: int,
    payload: ConversationDocumentAttach,
    user: CurrentUser,
    db: DbSession,
) -> dict[str, Any]:
    """Attach a workspace document to this conversation.

    Attaching the same document twice is a no-op rather than an error: the intent
    ("make this available here") is already satisfied, and raising would turn a
    harmless repeat click into a failure.
    """
    conversation = _require_conversation(db, user, conversation_id)

    document = db.scalar(
        select(Document).where(
            Document.id == payload.document_id,
            Document.workspace_id == conversation.workspace_id,
        )
    )
    if document is None:
        raise NotFound("That document is not in this conversation's workspace.")

    existing = db.scalar(
        select(ConversationDocument).where(
            ConversationDocument.conversation_id == conversation.id,
            ConversationDocument.document_id == document.id,
        )
    )
    if existing is not None:
        # Re-attaching reactivates, which is what "add it back" means.
        existing.is_active = True
    else:
        db.add(
            ConversationDocument(
                conversation_id=conversation.id, document_id=document.id, is_active=True
            )
        )

    db.commit()
    return scope_payload(db, conversation)


@router.patch("/conversations/{conversation_id}/documents/{document_id}")
def set_conversation_document_active(
    conversation_id: int,
    document_id: int,
    payload: ConversationDocumentToggle,
    user: CurrentUser,
    db: DbSession,
) -> dict[str, Any]:
    """Include or exclude one attached document from retrieval."""
    conversation = _require_conversation(db, user, conversation_id)

    link = db.scalar(
        select(ConversationDocument).where(
            ConversationDocument.conversation_id == conversation.id,
            ConversationDocument.document_id == document_id,
        )
    )
    if link is None:
        raise NotFound("That document is not attached to this conversation.")

    link.is_active = payload.is_active
    db.commit()
    return scope_payload(db, conversation)


@router.delete(
    "/conversations/{conversation_id}/documents/{document_id}", status_code=status.HTTP_200_OK
)
def detach_conversation_document(
    conversation_id: int, document_id: int, user: CurrentUser, db: DbSession
) -> dict[str, Any]:
    """Remove a document from this chat.

    NOT a deletion. The document, its chunks and its vectors stay in the workspace;
    only the link is removed. The response says so explicitly, because a user who
    thinks they deleted a file and did not - or thinks they did not and did - has
    been misled either way.
    """
    conversation = _require_conversation(db, user, conversation_id)

    link = db.scalar(
        select(ConversationDocument).where(
            ConversationDocument.conversation_id == conversation.id,
            ConversationDocument.document_id == document_id,
        )
    )
    if link is None:
        raise NotFound("That document is not attached to this conversation.")

    db.delete(link)
    db.commit()

    payload = scope_payload(db, conversation)
    payload["removed_from_chat_only"] = True
    payload["note"] = (
        "Removed from this chat. The document is still in the workspace and can be "
        "attached again."
    )
    return payload
