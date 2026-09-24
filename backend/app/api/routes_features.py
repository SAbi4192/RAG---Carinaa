"""
Response-experience routes: translate, shorten, read aloud, explain.

THE SHAPE OF EVERY ENDPOINT HERE
--------------------------------
    canonical grounded answer (Message.content)
        |
        +-- POST /features/translate  -> an AnswerVariant, cached
        +-- POST /features/shorten    -> an AnswerVariant, cached
        +-- POST /features/speech     -> plain speakable text, nothing stored
        +-- POST /features/explain    -> a learning-mode explanation, cached

Every one of them returns `original_unchanged: true`, and that is a promise the
code actually keeps: none of these handlers ever writes to `Message.content`. The
canonical answer is immutable after generation (spec section 60).
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFound
from app.core.logging import get_logger
from app.db.models import AnswerVariant, Conversation, Message
from app.features.languages import as_list as languages_as_list
from app.features.languages import get_language
from app.features.read_aloud import prepare_speech
from app.features.shorten import level_options, shorten_answer
from app.features.translate import translate_answer, translate_text
from app.llm.adapter import get_llm_adapter
from app.llm.base import LLMError
from app.rag.prompts import build_explain_messages
from app.schemas.chat import (
    ExplainOut,
    ExplainRequest,
    LanguagesOut,
    ShortenLevelsOut,
    ShortenRequest,
    SpeechOut,
    SpeechRequest,
    TextTranslateOut,
    TextTranslateRequest,
    TranslateRequest,
    VariantOut,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/features", tags=["features"])


def _require_message(db: DbSession, user: CurrentUser, message_id: int) -> Message:
    message = db.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.id == message_id, Conversation.user_id == user.id)
    )
    if message is None:
        raise NotFound("That message does not exist.")
    if message.role != "assistant":
        raise NotFound("Only answers can be transformed.")
    return message


def _mode_for(db: DbSession, message: Message) -> str:
    conversation = db.get(Conversation, message.conversation_id)
    return (conversation.ai_mode if conversation else None) or settings.default_ai_mode


def _resolve_mode(db: DbSession, user: CurrentUser, requested: str | None) -> str:
    """The AI mode for a request that is not tied to a stored message.

    Same precedence as `/chat/ask`: an explicit request wins, then the user's saved
    preference, then the server default. It matters here because Offline mode must
    refuse to translate rather than quietly reaching for an online provider - so
    guessing "online" would break the product's central guarantee.
    """
    if requested in ("online", "offline"):
        return requested
    preference = (user.preferences or {}).get("default_ai_mode")
    if preference in ("online", "offline"):
        return preference
    return settings.default_ai_mode


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
@router.get("/languages", response_model=LanguagesOut)
def list_languages() -> LanguagesOut:
    """Supported translation languages, straight from the single source of truth."""
    return LanguagesOut(languages=languages_as_list(), default="en")


@router.get("/shorten-levels", response_model=ShortenLevelsOut)
def list_shorten_levels() -> ShortenLevelsOut:
    return ShortenLevelsOut(levels=level_options())


@router.get("/capabilities")
def capabilities() -> dict:
    """What the toolbar should enable. Drives the UI, so it cannot drift."""
    return {
        "translation": {
            "enabled": settings.translation_enabled,
            "cached": settings.translate_cache_enabled,
            "languages": [lang["code"] for lang in languages_as_list()],
        },
        "shorten": {
            "enabled": settings.shorten_enabled,
            "levels": [level["level"] for level in level_options()],
        },
        "read_aloud": {
            "enabled": settings.read_aloud_enabled,
            "engine": "browser-speech-synthesis",
            "works_offline": True,
            "sends_data_externally": False,
        },
        "explain": {"enabled": True, "mode": "learning"},
        "note": (
            "All four are presentation transforms. None of them modifies the canonical "
            "grounded answer or re-runs retrieval."
        ),
    }


# ---------------------------------------------------------------------------
# Translate
# ---------------------------------------------------------------------------
@router.post("/translate", response_model=VariantOut)
async def translate(
    payload: TranslateRequest, user: CurrentUser, db: DbSession
) -> VariantOut:
    message = _require_message(db, user, payload.message_id)
    mode = _mode_for(db, message)

    outcome = await translate_answer(
        db, message=message, target_language=payload.language, mode=mode
    )

    return VariantOut(
        message_id=message.id,
        kind="translated",
        language=outcome.language,
        level="",
        content=outcome.content,
        citations=message.citations or [],
        validation=outcome.validation,
        provider=outcome.provider,
        model=outcome.model,
        cached=outcome.cached,
        warning=outcome.warning,
        original_unchanged=True,
    )


# ---------------------------------------------------------------------------
# Translate arbitrary text (Learning Mode panel)
# ---------------------------------------------------------------------------
@router.post("/translate-text", response_model=TextTranslateOut)
async def translate_free_text(
    payload: TextTranslateRequest, user: CurrentUser, db: DbSession
) -> TextTranslateOut:
    """Translate the Learning panel's explanation into another language.

    Deliberately NOT `/features/translate`: that endpoint is keyed to a stored
    answer and writes an `AnswerVariant`. The Learning explanation is teaching
    prose, not a grounded answer, and storing it as a variant of one would blur the
    line this project exists to keep - a variant is a presentation of the ANSWER,
    and nothing else. So this handler translates text and stores nothing.

    `user` is required even though no row is written: the endpoint calls an LLM, and
    an unauthenticated endpoint that spends provider quota is not acceptable.
    """
    mode = _resolve_mode(db, user, payload.mode)

    outcome = await translate_text(
        text=payload.text, target_language=payload.language, mode=mode
    )

    return TextTranslateOut(
        language=outcome.language,
        content=outcome.content,
        provider=outcome.provider,
        model=outcome.model,
        warning=outcome.warning,
    )


# ---------------------------------------------------------------------------
# Shorten
# ---------------------------------------------------------------------------
@router.post("/shorten", response_model=VariantOut)
async def shorten(payload: ShortenRequest, user: CurrentUser, db: DbSession) -> VariantOut:
    message = _require_message(db, user, payload.message_id)
    mode = _mode_for(db, message)

    outcome = await shorten_answer(db, message=message, level=payload.level, mode=mode)

    return VariantOut(
        message_id=message.id,
        kind="shortened",
        language="en",
        level=outcome.level,
        content=outcome.content,
        citations=message.citations or [],
        validation={
            **outcome.validation,
            "reduction_percent": outcome.reduction_percent,
            "original_length": outcome.original_length,
            "output_length": outcome.output_length,
        },
        provider=outcome.provider,
        model=outcome.model,
        cached=outcome.cached,
        warning=outcome.warning,
        original_unchanged=True,
    )


# ---------------------------------------------------------------------------
# Read Aloud
# ---------------------------------------------------------------------------
@router.post("/speech", response_model=SpeechOut)
def speech(payload: SpeechRequest, user: CurrentUser, db: DbSession) -> SpeechOut:
    """Prepare speakable text. Nothing is stored and no model is called.

    The client speaks it with the device's own voices, so this endpoint works
    identically in Online and Offline mode.
    """
    message = _require_message(db, user, payload.message_id)

    # If a translation of this message exists for the requested language, read that
    # instead - the user is looking at the translated text and expects to hear it.
    content = message.content
    if payload.language and payload.language != "en":
        variant = db.scalar(
            select(AnswerVariant).where(
                AnswerVariant.message_id == message.id,
                AnswerVariant.kind == "translated",
                AnswerVariant.language == payload.language,
            )
        )
        if variant is not None:
            content = variant.content

    result = prepare_speech(content, payload.language)

    return SpeechOut(
        message_id=message.id,
        language=result.language,
        speech_code=result.speech_code,
        speech_candidates=result.speech_candidates,
        text=result.text,
        characters=result.characters,
        voice_hint=result.voice_hint,
    )


# ---------------------------------------------------------------------------
# Explain (Learning Mode)
# ---------------------------------------------------------------------------
@router.post("/explain", response_model=ExplainOut)
async def explain(payload: ExplainRequest, user: CurrentUser, db: DbSession) -> ExplainOut:
    """Explain how this answer was produced, for a student new to RAG."""
    message = _require_message(db, user, payload.message_id)
    mode = _mode_for(db, message)

    # Cache it - the explanation for a given answer never changes.
    cached = db.scalar(
        select(AnswerVariant).where(
            AnswerVariant.message_id == message.id,
            AnswerVariant.kind == "explanation",
        )
    )
    if cached is not None:
        return ExplainOut(
            message_id=message.id,
            explanation=cached.content,
            provider=cached.provider,
            model=cached.model,
            cached=True,
        )

    # Rebuild the evidence list from what was actually retrieved and stored.
    retrieval = message.retrieval or {}
    excerpts = [
        {
            "number": index + 1,
            "label": chunk.get("label", ""),
            "content": chunk.get("content", "")[:1200],
        }
        for index, chunk in enumerate(retrieval.get("chunks", []))
    ]

    conversation = db.get(Conversation, message.conversation_id)
    question = ""
    if conversation is not None:
        previous = db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.role == "user",
                Message.created_at <= message.created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        ).first()
        question = previous.content if previous else ""

    grounding = message.grounding_detail or {}

    adapter = get_llm_adapter()
    try:
        response = await adapter.generate(
            build_explain_messages(question, excerpts, message.content, grounding),
            mode=mode,
            temperature=0.3,
            max_tokens=700,
        )
    except LLMError as exc:
        logger.warning("Explain failed: %s", exc)
        return ExplainOut(
            message_id=message.id,
            explanation=(
                "The explanation could not be generated right now. The RAG Trace tab "
                "shows the same information as raw, measured data."
            ),
            cached=False,
        )

    db.add(
        AnswerVariant(
            message_id=message.id,
            kind="explanation",
            language="en",
            level="",
            content=response.text.strip(),
            variant_metadata={"question": question[:400]},
            provider=response.provider,
            model=response.model,
            validation={},
        )
    )
    db.commit()

    return ExplainOut(
        message_id=message.id,
        explanation=response.text.strip(),
        provider=response.provider,
        model=response.model,
        used_fallback=response.used_fallback,
        cached=False,
    )


# ---------------------------------------------------------------------------
# Variant listing
# ---------------------------------------------------------------------------
@router.get("/messages/{message_id}/variants", response_model=list[VariantOut])
def list_variants(message_id: int, user: CurrentUser, db: DbSession) -> list[VariantOut]:
    """Everything cached for this message, so the UI can offer instant switching."""
    message = _require_message(db, user, message_id)

    rows = db.scalars(
        select(AnswerVariant)
        .where(AnswerVariant.message_id == message.id)
        .order_by(AnswerVariant.created_at)
    ).all()

    return [
        VariantOut(
            message_id=message.id,
            kind=row.kind,
            language=row.language,
            level=row.level,
            content=row.content,
            citations=row.citations or [],
            validation=row.validation or {},
            provider=row.provider,
            model=row.model,
            cached=True,
            original_unchanged=True,
        )
        for row in rows
        if row.kind in ("translated", "shortened")
    ]


@router.delete("/messages/{message_id}/variants", status_code=status.HTTP_204_NO_CONTENT)
def clear_variants(message_id: int, user: CurrentUser, db: DbSession) -> None:
    """Forget cached translations and shortenings for this message."""
    message = _require_message(db, user, message_id)
    rows = db.scalars(
        select(AnswerVariant).where(
            AnswerVariant.message_id == message.id,
            AnswerVariant.kind.in_(("translated", "shortened")),
        )
    ).all()
    for row in rows:
        db.delete(row)
    db.commit()


# Small convenience so the frontend can show the right speech tag without a round trip.
@router.get("/languages/{code}")
def language_detail(code: str) -> dict:
    language = get_language(code)
    return {
        "code": language.code,
        "name": language.name,
        "native_name": language.native_name,
        "speech_code": language.speech_code,
        "rtl": language.rtl,
    }
