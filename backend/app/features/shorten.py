"""
Shorten / condense a grounded answer (spec sections 34-38).

THIS IS COMPRESSION, NOT REGENERATION
-------------------------------------
The distinction matters more than it sounds.

Regeneration: "here is my evidence, write me an answer" -> a NEW answer, which may
differ from the one the user is reading.

Compression: "here is my answer, make it shorter without changing what it says"
-> the SAME answer, tighter.

Carinaa does the second. The user has already read and trusted the original; a
button labelled "Shorten" must not quietly hand them a different answer.

WHAT MUST SURVIVE COMPRESSION
-----------------------------
Every factual claim, every citation, every number, every caveat, every warning,
every technical term, and the conclusion. What goes is repetition, padding and
explanatory scaffolding.

VERIFICATION (spec section 38)
------------------------------
We do not trust the model to have obeyed. After compressing we compare the output
against the original and check:
  * citations preserved
  * numbers preserved
  * key vocabulary coverage above a floor
  * output genuinely shorter

If any of those fail, we REJECT the compression and keep the original. A user who
clicks Shorten and gets the original back with an explanation has lost nothing. A
user who gets a subtly-wrong compression has been misled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ShortenError, ShortenRejected
from app.core.logging import get_logger
from app.db.models import AnswerVariant, Message
from app.features.validation import validate_shortening
from app.llm.adapter import get_llm_adapter
from app.llm.base import LLMError
from app.rag.prompts import build_shorten_messages

logger = get_logger(__name__)

VALID_LEVELS: tuple[str, ...] = ("normal", "short", "very_short")

LEVEL_LABELS: dict[str, str] = {
    "normal": "Normal",
    "short": "Short",
    "very_short": "Very short",
}

LEVEL_DESCRIPTIONS: dict[str, str] = {
    "normal": "Tightened prose. Full explanation retained, filler removed.",
    "short": "Main answer plus the essential supporting details.",
    "very_short": "Core answer with its essential citations.",
}

# Minimum share of the original's key vocabulary that must survive. Below this we
# assume meaning was lost, not just words.
_MIN_COVERAGE: dict[str, float] = {
    "normal": 0.70,
    "short": 0.55,
    "very_short": 0.40,
}

# Guard against a "shortening" that barely shortens.
_MAX_RATIO: dict[str, float] = {
    "normal": 0.90,
    "short": 0.70,
    "very_short": 0.55,
}


@dataclass
class ShortenOutcome:
    content: str
    level: str
    provider: str
    model: str
    cached: bool
    validation: dict[str, Any]
    warning: str = ""
    original_length: int = 0
    output_length: int = 0

    @property
    def reduction_percent(self) -> float:
        if not self.original_length:
            return 0.0
        return round((1 - self.output_length / self.original_length) * 100, 1)


async def shorten_answer(
    db: Session,
    *,
    message: Message,
    level: str,
    mode: str,
) -> ShortenOutcome:
    """Compress a canonical answer, validating before we let the user see it."""
    if not settings.shorten_enabled:
        raise ShortenError("Shortening is disabled on this server.")

    normalized_level = (level or "short").strip().lower()
    if normalized_level not in VALID_LEVELS:
        raise ShortenError(
            f"'{level}' is not a valid shortening level.",
            detail={"valid_levels": list(VALID_LEVELS)},
        )

    # ---- cache lookup -----------------------------------------------------
    existing = db.scalar(
        select(AnswerVariant).where(
            AnswerVariant.message_id == message.id,
            AnswerVariant.kind == "shortened",
            AnswerVariant.language == "en",
            AnswerVariant.level == normalized_level,
        )
    )
    if existing is not None:
        return ShortenOutcome(
            content=existing.content,
            level=normalized_level,
            provider=existing.provider,
            model=existing.model,
            cached=True,
            validation=existing.validation or {},
            original_length=len(message.content),
            output_length=len(existing.content),
        )

    original = message.content.strip()
    if len(original) < 240:
        # Nothing meaningful to compress. Say so rather than making it worse.
        return ShortenOutcome(
            content=original,
            level=normalized_level,
            provider="none",
            model="",
            cached=True,
            validation={
                "passed": True,
                "note": "This answer is already short enough to compress meaningfully.",
            },
            warning="This answer is already concise, so it was left unchanged.",
            original_length=len(original),
            output_length=len(original),
        )

    adapter = get_llm_adapter()

    try:
        response = await adapter.generate(
            build_shorten_messages(original, normalized_level),
            mode=mode,
            # Compression should be conservative, not creative.
            temperature=0.1,
            max_tokens=max(512, len(original) // 2),
        )
    except LLMError as exc:
        raise ShortenError(
            "The answer could not be compressed. The original is unchanged.",
            detail={"reason": exc.__class__.__name__},
        ) from exc

    compressed = response.text.strip()
    if not compressed:
        raise ShortenError("The compressed version came back empty. The original is unchanged.")

    # ---- validate ---------------------------------------------------------
    validation = validate_shortening(
        original,
        compressed,
        level=normalized_level,
        min_concept_coverage=_MIN_COVERAGE[normalized_level],
    )

    # A truncated response means the model never finished compressing. Even when the
    # text we received happens to satisfy every check, we did not get a *completed*
    # compression, so we cannot honestly call it verified. Reject it and keep the
    # original - the same rule as any other failed validation. See
    # `gemini_thinking_budget` in config.py for why this happens.
    if response.truncated:
        validation.passed = False
        validation.issues.append(
            "The provider stopped generating before it finished, so this compression "
            "could not be verified as complete."
        )

    ratio = len(compressed) / max(1, len(original))
    if ratio > _MAX_RATIO[normalized_level]:
        validation.passed = False
        validation.issues.append(
            f"The result is {ratio:.0%} of the original length, which is not short enough "
            f"for the '{LEVEL_LABELS[normalized_level]}' level (expected at most "
            f"{_MAX_RATIO[normalized_level]:.0%})."
        )

    if not validation.passed:
        # Reject the compression outright. Keeping the original is always safe;
        # showing an unverified compression is not.
        logger.warning(
            "Rejected a shortening of message %s: %s", message.id, validation.issues
        )
        raise ShortenRejected(
            "The shortened version could not be verified as preserving the original's "
            "meaning and citations, so the original answer has been kept.",
            detail={"issues": validation.issues, "validation": validation.as_dict()},
        )

    # ---- cache ------------------------------------------------------------
    variant = AnswerVariant(
        message_id=message.id,
        kind="shortened",
        language="en",
        level=normalized_level,
        content=compressed,
        citations=message.citations or [],
        variant_metadata={
            "source_length": len(original),
            "output_length": len(compressed),
            "reduction_percent": round((1 - ratio) * 100, 1),
        },
        provider=response.provider,
        model=response.model,
        validation=validation.as_dict(),
    )
    db.add(variant)
    db.commit()

    return ShortenOutcome(
        content=compressed,
        level=normalized_level,
        provider=response.provider,
        model=response.model,
        cached=False,
        validation=validation.as_dict(),
        original_length=len(original),
        output_length=len(compressed),
    )


def level_options() -> list[dict[str, str]]:
    """Level metadata for the frontend, so labels cannot drift."""
    return [
        {
            "level": level,
            "label": LEVEL_LABELS[level],
            "description": LEVEL_DESCRIPTIONS[level],
            "target_reduction": f"{round((1 - _MAX_RATIO[level]) * 100)}% or more",
        }
        for level in VALID_LEVELS
    ]
