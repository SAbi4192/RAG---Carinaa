"""
Detailed Answer: re-present a stored answer in an exam-shaped format.

HOW THIS DIFFERS FROM A SECOND QUESTION
---------------------------------------
When the user chooses 8-Mark / 16-Mark / 20-Mark / University Style / More Detail
on an answer card, we do NOT re-run retrieval and we do NOT ask the web again. The
evidence that grounded the ORIGINAL answer is stored on the message
(`Message.retrieval["chunks"]`, `Message.web_sources`). This feature rebuilds the
numbered CONTEXT from exactly those chunks - the same numbers, the same order -
and asks the model to rewrite only the PRESENTATION.

Three consequences, all deliberate:

  1. Citations stay honest. [1] in the long answer is [1] in the short answer,
     because they resolve against the same excerpt list via the same
     `resolve_citations()`. A long answer that quietly renumbered its sources
     would be the worst possible failure mode for an exam tool.
  2. Retrieval is untouched. A style is a depth decision, not a second opinion.
     Re-running retrieval could change the evidence, which would make "the
     Detailed Answer for THIS response" a false claim.
  3. If the model cites something that was not in the evidence, the variant is
     rejected and the caller is told - the canonical answer is never modified.

The canonical answer stays immutable (spec section 60): this writes an
`AnswerVariant` with kind="detailed", exactly like shorten/translate do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DetailedAnswerError, DetailedAnswerRejected
from app.core.logging import get_logger
from app.db.models import AnswerVariant, Message
from app.llm.adapter import get_llm_adapter
from app.llm.base import LLMError
from app.rag.citations import resolve_citations
from app.rag.grounding import check_grounding
from app.rag.prompts import ANSWER_STYLES, build_generation_messages

logger = get_logger(__name__)


@dataclass
class DetailedOutcome:
    content: str
    style: str
    label: str
    provider: str
    model: str
    cached: bool
    citations: list[dict[str, Any]] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    warning: str = ""


def _style_or_raise(style: str) -> tuple[str, dict[str, Any]]:
    normalized = (style or "").strip().lower()
    definition = ANSWER_STYLES.get(normalized)
    if definition is None:
        raise DetailedAnswerError(
            f"'{style}' is not a detailed answer style.",
            detail={"valid_styles": list(ANSWER_STYLES)},
        )
    return normalized, definition


def _evidence_excerpts(message: Message) -> list[dict[str, Any]]:
    """Rebuild the numbered excerpt list from what the message actually stored.

    The stored chunks already carry their `number`-defining order (the context
    builder numbered them by final score and the retrieval payload preserves that
    order), so enumerating `retrieval["chunks"]` from 1 reproduces the ORIGINAL
    numbering rather than approximating it.
    """
    retrieval = message.retrieval or {}
    chunks = retrieval.get("chunks") or []
    excerpts: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue
        excerpts.append(
            {
                "number": index + 1,
                "chunk_id": chunk.get("chunk_id"),
                "document_id": chunk.get("document_id"),
                "document_name": chunk.get("document_name", ""),
                "file_type": chunk.get("file_type", ""),
                "content": str(chunk.get("content") or ""),
                "metadata": chunk.get("metadata") or {},
                "label": chunk.get("label", ""),
            }
        )
    return excerpts


def _web_sources_for(message: Message) -> list[dict[str, Any]]:
    """The web block for re-generation, exactly as the original request saw it.

    Only returns sources when the ORIGINAL answer used the web; a detailed answer
    must never introduce web content the user did not opt into for that question.
    """
    if not message.web_search_used:
        return []
    raw = message.web_sources or []
    return [s for s in raw if isinstance(s, dict) and s.get("number") is not None]


async def generate_detailed_answer(
    db: Session,
    *,
    message: Message,
    question: str,
    style: str,
    mode: str,
) -> DetailedOutcome:
    """Produce (or load from cache) the detailed variant for `style`."""
    normalized, definition = _style_or_raise(style)

    # ---- cache lookup -------------------------------------------------------
    existing = db.scalar(
        select(AnswerVariant).where(
            AnswerVariant.message_id == message.id,
            AnswerVariant.kind == "detailed",
            AnswerVariant.language == "en",
            AnswerVariant.level == normalized,
        )
    )
    if existing is not None:
        return DetailedOutcome(
            content=existing.content,
            style=normalized,
            label=definition["label"],
            provider=existing.provider,
            model=existing.model,
            cached=True,
            citations=existing.citations or [],
            validation=existing.validation or {},
        )

    excerpts = _evidence_excerpts(message)
    if not excerpts:
        # No stored evidence means there is nothing to ground a longer answer on.
        # Regenerating from the short answer alone would let the model expand it
        # from memory, which is exactly what the pipeline exists to prevent.
        raise DetailedAnswerError(
            "This answer has no stored evidence to expand from, so a Detailed "
            "Answer cannot be produced without inventing content.",
            detail={"reason": "no_evidence"},
        )

    web_sources = _web_sources_for(message)

    # Same builder as the production pipeline: same CONTEXT block, same rules,
    # same style guidance. Re-implementing the prompt here is how drift starts.
    messages = build_generation_messages(
        question or message.content[:200],
        excerpts,
        history=None,
        language="en",
        web_sources=web_sources,
        mode=mode,
        answer_style=normalized,
    )

    adapter = get_llm_adapter()
    try:
        response = await adapter.generate(
            messages,
            mode=mode,
            temperature=definition["temperature"],
            max_tokens=definition["max_tokens"],
        )
    except LLMError as exc:
        logger.warning("Detailed answer generation failed: %s", exc)
        raise DetailedAnswerError(
            "The detailed version could not be generated. The original answer is "
            "unchanged.",
            detail={"reason": exc.__class__.__name__},
        ) from exc

    expanded = response.text.strip()
    if not expanded:
        raise DetailedAnswerError(
            "The detailed version came back empty. The original is unchanged."
        )

    # ---- citation + grounding validation ------------------------------------
    # The variant is only honest if every [n] it cites exists in the SAME evidence
    # the original cited. Fabricated markers are rejected, never shipped.
    report = resolve_citations(expanded, excerpts, web_sources=web_sources)
    grounding = check_grounding(
        expanded,
        excerpts,
        report,
        top_score=(message.retrieval or {}).get("top_score", 0.0),
        score_scale=(message.retrieval or {}).get("score_scale", "cosine"),
    )

    validation = {
        "passed": report.valid,
        "cited_numbers": report.cited_numbers,
        "invalid_numbers": report.invalid_numbers,
        "grounding_status": grounding.status,
        "supported_count": grounding.supported_count,
        "weak_count": grounding.weak_count,
        "uncited_count": grounding.uncited_count,
    }

    if not report.valid:
        validation["passed"] = False
        raise DetailedAnswerRejected(
            detail={
                "reason": "invalid_citations",
                "invalid_numbers": report.invalid_numbers,
                "errors": report.errors[:3],
            },
        )

    variant = AnswerVariant(
        message_id=message.id,
        kind="detailed",
        language="en",
        level=normalized,
        content=expanded,
        citations=[c.as_dict() for c in report.citations],
        variant_metadata={"question": (question or "")[:400], "style_label": definition["label"]},
        provider=response.provider,
        model=response.model,
        validation=validation,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)

    return DetailedOutcome(
        content=expanded,
        style=normalized,
        label=definition["label"],
        provider=response.provider,
        model=response.model,
        cached=False,
        citations=[c.as_dict() for c in report.citations],
        validation=validation,
        warning=(
            ""
            if grounding.is_supported
            else "Some claims in the detailed version are weakly supported by the "
            "original evidence. Check it against the sources."
        ),
    )
