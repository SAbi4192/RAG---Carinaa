"""
Database models.

WHY THIS FILE EXISTS
--------------------
Carinaa needs durable storage for things a vector database is bad at: users,
workspaces, documents, conversations, and the audit trail of what the RAG
pipeline actually did.

DIVISION OF RESPONSIBILITY
--------------------------
  SQLite (here)        -> users, workspaces, documents, chunks (text + metadata),
                          conversations, messages, answer variants, trace events
  Vector DB (Chroma)   -> embeddings + the minimum metadata needed to filter

Both are joined by `chunk.vector_id`. The vector store is the *index*; SQLite is
the *source of truth*. That means we can always re-index from SQLite without
re-uploading anything - which is exactly what happens when the embedding model
changes.

WORKSPACE ISOLATION
-------------------
`workspace_id` is denormalised onto `Document` and `Chunk` on purpose. It lets
the retrieval layer apply a hard workspace filter in a single place, and lets us
verify after retrieval that nothing crossed a boundary (spec sections 18, 66).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all Carinaa tables."""

    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


# ===========================================================================
# Identity
# ===========================================================================
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # User preferences (theme, preferred language, AI mode...). Kept as JSON so
    # adding a preference never needs a migration.
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    workspaces: Mapped[list["Workspace"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


# ===========================================================================
# Workspaces - the isolation boundary
# ===========================================================================
class Workspace(Base):
    """A knowledge base. Every document, vector and conversation belongs to one."""

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    color: Mapped[str] = mapped_column(String(20), default="violet", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    owner: Mapped[User] = relationship(back_populates="workspaces")
    documents: Mapped[list["Document"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_workspace_user_name"),)


# ===========================================================================
# Documents & ingestion state
# ===========================================================================
class Document(Base):
    """An uploaded file plus its *real* processing state.

    `stage` and `progress` mirror what the ingestion pipeline is actually doing.
    They are never advanced by a timer - see `app/ingestion/pipeline.py`.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    original_filename: Mapped[str] = mapped_column(String(400), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(400), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64), default="", index=True)

    # pending | processing | ready | failed
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # uploading|parsing|extracting|normalizing|chunking|embedding|indexing|ready|failed
    stage: Mapped[str] = mapped_column(String(30), default="uploading")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    page_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)

    # Which embedding model produced the vectors for this document. If the
    # configured model changes, this mismatch is what triggers a re-index.
    embedding_model: Mapped[str] = mapped_column(String(200), default="")
    embedding_dim: Mapped[int] = mapped_column(Integer, default=0)

    doc_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stage_timings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_documents_ws_status", "workspace_id", "status"),)


class Chunk(Base):
    """A retrievable unit of text plus the metadata needed to cite it.

    `doc_metadata` carries the provenance keys the spec requires:
    page_number, slide_number, sheet_name, section, row_start/row_end, json_path.
    The retrieval layer treats them as opaque - it never needs to know which
    parser produced them.
    """

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    workspace_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)

    # block type: paragraph | table_row | bullet | code | heading | json_leaf ...
    block_type: Mapped[str] = mapped_column(String(30), default="paragraph")

    doc_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    vector_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    embedding_model: Mapped[str] = mapped_column(String(200), default="")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_chunks_doc_index", "document_id", "chunk_index"),
        Index("ix_chunks_ws", "workspace_id"),
    )


# ===========================================================================
# Conversations
# ===========================================================================
class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    title: Mapped[str] = mapped_column(String(300), default="New conversation")
    ai_mode: Mapped[str] = mapped_column(String(20), default="online")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    workspace: Mapped[Workspace] = relationship(back_populates="conversations")
    owner: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    document_links: Mapped[list["ConversationDocument"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class ConversationDocument(Base):
    """A document attached to one conversation - "chat-scoped knowledge".

    WHY THIS IS A LINK TABLE AND NOT A COPY
    ---------------------------------------
    The same document can belong to a workspace and be attached to several
    conversations at once. Storing the file, its chunks or its vectors again per
    conversation would multiply storage, and re-embedding identical text would
    produce identical vectors - pure waste. So this table records only the
    RELATIONSHIP. The document, its chunks and its vectors exist exactly once.

    `is_active` is the selection within the chat: a conversation may have five
    documents attached while only two are searched for the current question.
    Detaching (`is_active = False`) is not deletion - it narrows the retrieval
    scope. That distinction matters, because "stop using this here" and "delete
    this" are very different requests and the UI must not conflate them.

    Enforced at RETRIEVAL time, not in the prompt: the retriever filters by
    document id before vector search, so an out-of-scope document cannot reach the
    context even if the model were inclined to use it.
    """

    __tablename__ = "conversation_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    added_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="document_links")
    document: Mapped["Document"] = relationship()

    __table_args__ = (
        # One link per (conversation, document). Attaching twice is a no-op rather
        # than a duplicate row that would double-count in the UI.
        UniqueConstraint("conversation_id", "document_id", name="uq_conversation_document"),
    )


class Message(Base):
    """One turn. For assistant turns this row holds the CANONICAL grounded answer.

    Section 60 (canonical answer principle): `content` is the source of truth.
    Translations and shortenings live in `AnswerVariant` rows and never overwrite
    this column.
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )

    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # --- provenance of an assistant turn (all real, all measured) ---
    ai_mode: Mapped[str] = mapped_column(String(16), default="online")
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    used_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_reason: Mapped[str] = mapped_column(Text, default="")

    grounding_status: Mapped[str] = mapped_column(String(30), default="")
    grounding_detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    trace_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    retrieval: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    citations: Mapped[list[Any]] = mapped_column(JSON, default=list)

    web_search_used: Mapped[bool] = mapped_column(Boolean, default=False)
    web_sources: Mapped[list[Any]] = mapped_column(JSON, default=list)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    variants: Mapped[list["AnswerVariant"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )
    trace_events: Mapped[list["TraceEvent"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_messages_conv_created", "conversation_id", "created_at"),)


class AnswerVariant(Base):
    """A presentation transform of a canonical answer.

    kind = translated | shortened | detailed | explanation
    Cached so switching language, condensing level or Detailed Answer style is
    instant the second time. `level` holds the detailed style key when the kind
    is "detailed".
    """

    __tablename__ = "answer_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    language: Mapped[str] = mapped_column(String(12), default="en")
    level: Mapped[str] = mapped_column(String(20), default="")  # normal|short|very_short

    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    citations: Mapped[list[Any]] = mapped_column(JSON, default=list)
    variant_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    message: Mapped[Message] = relationship(back_populates="variants")

    __table_args__ = (
        UniqueConstraint(
            "message_id", "kind", "language", "level", name="uq_variant_message_kind_lang_level"
        ),
    )


class TraceEvent(Base):
    """One real stage of one query. Powers RAG Trace.

    SECURITY: never store API keys, hidden prompts, or private chain-of-thought
    here. Only measured facts: stage, status, duration, counts, scores, model.
    """

    __tablename__ = "trace_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    trace_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    seq: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok|skipped|error
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    event_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    message: Mapped[Message] = relationship(back_populates="trace_events")

    __table_args__ = (Index("ix_trace_message_seq", "message_id", "seq"),)


# ===========================================================================
# Analytics / evaluation
# ===========================================================================
class QueryLog(Base):
    """Aggregate row per query for the Analytics page. Real numbers only."""

    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    ai_mode: Mapped[str] = mapped_column(String(16), default="online")
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")

    retrieved_count: Mapped[int] = mapped_column(Integer, default=0)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    top_score: Mapped[float] = mapped_column(Float, default=0.0)
    mean_score: Mapped[float] = mapped_column(Float, default=0.0)

    retrieval_ms: Mapped[int] = mapped_column(Integer, default=0)
    generation_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_ms: Mapped[int] = mapped_column(Integer, default=0)

    grounding_status: Mapped[str] = mapped_column(String(30), default="")
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    refused: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluationRun(Base):
    """A stored retrieval/evaluation result set.

    Populated only by actually running evaluation - never with invented scores.
    """

    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(200), default="Evaluation")
    k: Mapped[int] = mapped_column(Integer, default=5)
    dataset_size: Mapped[int] = mapped_column(Integer, default=0)

    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    details: Mapped[list[Any]] = mapped_column(JSON, default=list)

    embedding_model: Mapped[str] = mapped_column(String(200), default="")
    rerank_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
