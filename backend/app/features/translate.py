"""
Translation of a grounded answer (spec sections 25-30).

WHERE TRANSLATION SITS
----------------------
    RAG -> Generated Answer -> Grounding -> Citation Resolution
        -> Final Grounded Answer
        -> TRANSLATION LAYER        <-- here
        -> Translated Presentation

Translation happens AFTER grounding and citation resolution, never before. Two
reasons:

  1. Grounding is checked against the ORIGINAL answer in the language of the
     documents. Translating first would mean grounding a Tamil sentence against an
     English chunk, which our lexical check cannot do - and neither can most
     embedding models, reliably.

  2. The original remains the source of truth. The translation is a view of it.

WHAT TRANSLATION MUST NOT DO
----------------------------
  * re-run retrieval. There is no reason to search again: we already have the
    evidence and the answer. Re-retrieving would also risk producing a *different*
    answer under the same message, which would be deeply confusing.
  * overwrite the original. `Message.content` is untouched; the translation is an
    `AnswerVariant` row.
  * go online in Offline mode. See `translate_answer` below.

OFFLINE MODE (spec section 28)
------------------------------
When Offline mode is selected we use the LOCAL model to translate. If the local
model is unavailable, translation fails with a clear message. We never reach for an
online translation API - that would break the single most important guarantee in
the product.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import TranslationError, TranslationUnavailableOffline
from app.core.logging import get_logger
from app.db.models import AnswerVariant, Message
from app.features.languages import get_language, is_supported, translation_token_budget
from app.features.validation import validate_translation
from app.llm.adapter import get_llm_adapter
from app.llm.base import LLMError
from app.rag.prompts import build_translation_messages

logger = get_logger(__name__)


@dataclass
class TranslationOutcome:
    content: str
    language: str
    provider: str
    model: str
    cached: bool
    validation: dict[str, Any]
    warning: str = ""


async def translate_answer(
    db: Session,
    *,
    message: Message,
    target_language: str,
    mode: str,
) -> TranslationOutcome:
    """Translate a canonical answer, caching the result as a variant."""
    if not settings.translation_enabled:
        raise TranslationError("Translation is disabled on this server.")

    code = (target_language or "en").strip().lower()
    if not is_supported(code):
        raise TranslationError(
            f"'{target_language}' is not a supported language.",
            detail={"supported": ["en", "ta", "ml", "te", "hi", "ja", "it"]},
        )

    # English is the canonical language; asking for it is a no-op.
    if code == "en":
        return TranslationOutcome(
            content=message.content,
            language="en",
            provider="none",
            model="",
            cached=True,
            validation={"passed": True, "note": "The canonical answer is already English."},
        )

    # ---- cache lookup -----------------------------------------------------
    if settings.translate_cache_enabled:
        existing = db.scalar(
            select(AnswerVariant).where(
                AnswerVariant.message_id == message.id,
                AnswerVariant.kind == "translated",
                AnswerVariant.language == code,
            )
        )
        if existing is not None:
            return TranslationOutcome(
                content=existing.content,
                language=code,
                provider=existing.provider,
                model=existing.model,
                cached=True,
                validation=existing.validation or {},
            )

    if len(message.content) > settings.max_translate_chars:
        raise TranslationError(
            f"This answer is too long to translate in one pass "
            f"({len(message.content)} characters; limit is {settings.max_translate_chars}). "
            f"Try shortening it first."
        )

    language = get_language(code)
    adapter = get_llm_adapter()

    # Size the output budget for the TARGET script, not from the source length
    # alone. See `translation_token_budget`: a 552-character English answer became
    # 948 characters of Tamil and did not fit in the old 1024-token budget, so the
    # translation was cut off mid-sentence.
    budget = translation_token_budget(message.content, code)

    # ---- generate ---------------------------------------------------------
    try:
        response = await adapter.generate(
            build_translation_messages(
                message.content, code, target_language_name=language.name
            ),
            mode=mode,
            # Translation wants determinism, not creativity.
            temperature=0.0,
            max_tokens=budget,
        )
    except LLMError as exc:
        if mode == "offline":
            # Do NOT fall through to an online translator. Ever.
            raise TranslationUnavailableOffline(
                "Translation is unavailable in Offline mode because the local model "
                "could not be used. The original grounded answer is unchanged."
            ) from exc
        raise TranslationError(
            "The translation could not be generated. The original grounded answer is "
            "unchanged.",
            detail={"reason": exc.__class__.__name__},
        ) from exc

    translated = response.text.strip()
    if not translated:
        raise TranslationError("The translation came back empty. The original is unchanged.")

    # ---- validate ---------------------------------------------------------
    validation = validate_translation(message.content, translated)

    # A dropped citation marker is the one failure worth a second attempt.
    #
    # The prompt already forbids dropping markers, but models occasionally treat a
    # bracketed number as noise and quietly remove it. The consequence is not
    # cosmetic: the translated text would cite fewer sources than the answer it
    # renders, so the reader loses a way to verify a claim. One corrective retry
    # that NAMES the missing markers fixes this in practice far more often than it
    # costs, and it is bounded to a single extra call.
    if not validation.passed and validation.missing_citations:
        logger.info(
            "Translation dropped citation(s) %s; retrying with a correction.",
            validation.missing_citations,
        )
        retry_response = None
        try:
            retry_response = await adapter.generate(
                build_translation_messages(
                    message.content,
                    code,
                    target_language_name=language.name,
                    correction=validation.missing_citations,
                ),
                mode=mode,
                temperature=0.0,
                max_tokens=budget,
            )
        except LLMError as exc:
            # The first attempt still stands, and the warning still tells the truth.
            logger.warning("Translation retry failed: %s", exc)

        if retry_response and retry_response.text.strip():
            retry_validation = validate_translation(
                message.content, retry_response.text.strip()
            )
            # Only accept the retry if it is genuinely better. A retry that fixes
            # one marker while losing another is not an improvement.
            if len(retry_validation.missing_citations) < len(validation.missing_citations):
                translated = retry_response.text.strip()
                validation = retry_validation
                response = retry_response

    warning = ""
    if not validation.passed:
        # We still return the translation - it may be perfectly good - but we tell
        # the user exactly what our automated check flagged, and we keep the
        # original one click away.
        warning = " ".join(validation.issues)
        logger.warning("Translation validation flagged issues: %s", warning)

    # A truncated response is a second, quieter failure mode: the model stopped
    # because it ran out of output budget, not because it had finished. Our
    # validator may not notice - a translation can be cut short mid-sentence and
    # still contain every marker it needs. So we report what the provider told us
    # rather than guessing. See `gemini_thinking_budget` in config.py.
    if response.truncated:
        truncation_note = (
            "The provider stopped generating before it finished, so this translation "
            "may be incomplete. The original grounded answer is unchanged."
        )
        warning = f"{warning} {truncation_note}".strip()
        logger.warning("Translation was truncated by the provider's output limit.")

    # ---- cache ------------------------------------------------------------
    variant = AnswerVariant(
        message_id=message.id,
        kind="translated",
        language=code,
        level="",
        content=translated,
        citations=message.citations or [],
        variant_metadata={
            "source_length": len(message.content),
            "output_length": len(translated),
            "speech_code": language.speech_code,
            "native_name": language.native_name,
        },
        provider=response.provider,
        model=response.model,
        validation=validation.as_dict(),
    )
    db.add(variant)
    db.commit()

    return TranslationOutcome(
        content=translated,
        language=code,
        provider=response.provider,
        model=response.model,
        cached=False,
        validation=validation.as_dict(),
        warning=warning,
    )
