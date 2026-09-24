"""Chat, RAG trace, and presentation-feature schemas."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ShortenLevel = Literal["normal", "short", "very_short"]
AiMode = Literal["online", "offline"]


# ===========================================================================
# Conversations
# ===========================================================================
class ConversationCreate(BaseModel):
    workspace_id: int
    title: str = Field(default="New conversation", max_length=300)
    ai_mode: AiMode = "online"


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    ai_mode: AiMode | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    title: str
    ai_mode: str
    created_at: dt.datetime
    updated_at: dt.datetime
    message_count: int = 0


class ConversationListOut(BaseModel):
    conversations: list[ConversationOut]
    total: int


# ===========================================================================
# Asking a question
# ===========================================================================
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    conversation_id: int | None = None
    workspace_id: int

    mode: AiMode | None = None

    # Retrieval overrides for the Playground / Developer Mode
    top_k: int | None = Field(default=None, ge=1, le=50)
    candidate_k: int | None = Field(default=None, ge=1, le=200)
    document_ids: list[int] | None = None
    use_rerank: bool | None = None

    # Optional web augmentation. Explicitly opt-in; never implied.
    use_web_search: bool = False

    # Language for the generated answer (English by default)
    language: str = Field(default="en", max_length=8)


class CitationOut(BaseModel):
    number: int
    marker: str
    kind: str = "document"
    location_label: str = ""
    document_id: int | None = None
    document_name: str = ""
    file_type: str = ""
    chunk_id: int | None = None
    chunk_index: int | None = None
    page_number: int | None = None
    page_end: int | None = None
    slide_number: int | None = None
    sheet_name: str | None = None
    section: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    json_path: str | None = None
    snippet: str = ""
    relevance: float = 0.0
    rank: int = 0
    url: str = ""
    title: str = ""


class GroundingOut(BaseModel):
    status: str
    label: str
    tone: str
    reason: str = ""
    refused: bool = False
    checks: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    cited_numbers: list[int] = Field(default_factory=list)
    unused_numbers: list[int] = Field(default_factory=list)
    invalid_numbers: list[int] = Field(default_factory=list)
    citation_count: int = 0
    excerpt_count: int = 0
    top_score: float = 0.0
    disclaimer: str = ""


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: str
    content: str
    ai_mode: str
    provider: str = ""
    model: str = ""
    used_fallback: bool = False
    fallback_reason: str = ""
    grounding_status: str = ""
    grounding_detail: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = 0
    token_usage: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""
    retrieval: dict[str, Any] = Field(default_factory=dict)
    citations: list[Any] = Field(default_factory=list)
    web_search_used: bool = False
    web_sources: list[Any] = Field(default_factory=list)
    created_at: dt.datetime


class AskResponse(BaseModel):
    """The full result of one question, including everything the UI needs."""

    message: MessageOut
    conversation_id: int

    # Convenience duplicates of the most-used fields, so the UI does not have to
    # dig into nested objects for the common case.
    answer: str
    provider_label: str
    is_extractive_failsafe: bool = False

    grounding: GroundingOut | None = None
    citations: list[CitationOut] = Field(default_factory=list)
    retrieval: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
    web_sources: list[dict[str, Any]] = Field(default_factory=list)

    # Presentation transforms that are already cached and can be shown instantly
    available_variants: list[dict[str, Any]] = Field(default_factory=list)


# ===========================================================================
# RAG Trace
# ===========================================================================
class TraceStageOut(BaseModel):
    seq: int
    stage: str
    label: str
    status: str
    duration_ms: int
    data: dict[str, Any] = Field(default_factory=dict)
    # Wall-clock time the stage finished, for the log-style "Live RAG Trace" view.
    # Optional because an event recorded before this field existed has no value.
    created_at: str | None = None


class TraceOut(BaseModel):
    trace_id: str
    message_id: int
    event_count: int
    total_ms: int
    stages: list[TraceStageOut]
    summary: dict[str, Any] = Field(default_factory=dict)
    # The question THIS answer belongs to.
    #
    # Learning Mode is per-message: clicking an answer must show the question that
    # produced it, and everything below it (scope, context, diagram) has to belong
    # to that same turn. The client cannot work this out from the message alone -
    # an assistant row carries no question - so the server resolves the paired user
    # turn and returns it here.
    question: str = ""
    note: str = (
        "Every value here was measured while answering your question. Re-ranking and "
        "web search appear as 'skipped' when they did not run."
    )


# ===========================================================================
# Presentation features
# ===========================================================================
class TranslateRequest(BaseModel):
    message_id: int
    language: str = Field(min_length=2, max_length=8)


class TextTranslateRequest(BaseModel):
    """Translate a block of UI/teaching text (the Learning Mode explanation).

    This is deliberately NOT `TranslateRequest`: that one is keyed to a stored
    answer and caches an `AnswerVariant` against it. Learning Mode translates
    explanatory prose that was never a grounded answer and must not appear as a
    variant of one - so it takes the text directly and stores nothing.
    """

    text: str = Field(min_length=1, max_length=8000)
    language: str = Field(min_length=2, max_length=8)
    # The mode the caller is in. Offline refuses rather than reaching for an online
    # translator, exactly as answer translation does.
    mode: AiMode | None = None


class TextTranslateOut(BaseModel):
    language: str
    content: str
    provider: str = ""
    model: str = ""
    warning: str = ""
    original_unchanged: bool = True


class ShortenRequest(BaseModel):
    message_id: int
    level: ShortenLevel = "short"


class VariantOut(BaseModel):
    message_id: int
    kind: str
    language: str = "en"
    level: str = ""
    content: str
    citations: list[Any] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)
    provider: str = ""
    model: str = ""
    cached: bool = False
    warning: str = ""
    original_unchanged: bool = True


class ExplainRequest(BaseModel):
    message_id: int


class ExplainOut(BaseModel):
    message_id: int
    explanation: str
    provider: str = ""
    model: str = ""
    used_fallback: bool = False
    cached: bool = False


class SpeechRequest(BaseModel):
    message_id: int
    language: str = "en"


class SpeechOut(BaseModel):
    message_id: int
    language: str
    speech_code: str
    #: Ordered preferred BCP-47 tags. The client tries each before declaring no
    #: matching voice, so "exactly this regional tag is absent, but a sibling
    #: exists" is a normal read, not a fallback event. See `languages.py`.
    speech_candidates: list[str] = []
    text: str
    characters: int
    voice_hint: str = ""
    note: str = (
        "Read Aloud uses your browser's built-in speech synthesis, which runs on your "
        "device. Carinaa does not send the answer to an external speech service."
    )


class LanguagesOut(BaseModel):
    languages: list[dict[str, Any]]
    default: str


class ShortenLevelsOut(BaseModel):
    levels: list[dict[str, str]]


# ===========================================================================
# Playground / retrieval-only
# ===========================================================================
class RetrieveRequest(BaseModel):
    workspace_id: int
    question: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    candidate_k: int | None = Field(default=None, ge=1, le=200)
    document_ids: list[int] | None = None
    use_rerank: bool | None = None
    # Optional. When present, the conversation's chat scope applies unless
    # `document_ids` overrides it. Without this the retrieval-only endpoint would
    # ignore the scope the chat enforces, so the Retrieval Lab would show chunks
    # that a real answer could never use - the two views would disagree.
    conversation_id: int | None = None
    # Optional structural filters, so the Reference and Scope labs can demonstrate
    # them directly. These are the same filters /chat/ask applies, resolved the same
    # way, so a lab result cannot differ from what a real answer would search.
    page_number: int | None = Field(default=None, ge=1, le=10000)
    section: str | None = None
    # Optional retriever mode, so the Retrieval Lab can demonstrate dense / bm25 /
    # hybrid over the SAME question and scope a real answer would use. None means
    # "use the configured default", identical to the pipeline.
    mode: str | None = Field(default=None)


class RetrieveResponse(BaseModel):
    retrieval: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
    generated: bool = False
    note: str = ""
    error: str = ""
