"""
Offline extractive fail-safe.

WHY THIS EXISTS
---------------
Spec section 15 says that if the local LLM is unavailable, offline mode must show
a clear message OR use "a clearly labelled local extractive/evidence-only failsafe".
It must never silently go online.

This module is that fail-safe.

WHAT IT DOES
------------
It builds an answer by *selecting and quoting* the most relevant sentences from the
retrieved evidence, with their citation markers attached. No generation happens. No
network call is made. It is pure local text processing.

WHAT IT IS NOT
--------------
It is not an LLM answer, and it never pretends to be. The response carries
`is_extractive_failsafe = True`, the UI shows an "Extractive (no model)" badge, and
the text itself opens with a line saying so.

Being honest about the degraded mode is the whole point. A fail-safe that pretends
to be the real thing would be worse than no fail-safe at all, because the user would
trust it more than they should.

This is also genuinely useful for the demo: you can show the instructor that even
with the model removed, the retrieval + citation architecture still works. The
evidence is real; only the prose is missing.
"""

from __future__ import annotations

import re
from typing import Any

from app.rag.citations import CITATION_RE, strip_code

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")

_STOPWORDS = frozenset(
    """a an the and or but if then than that this these those is are was were be been
being am do does did doing have has had having will would shall should can could
may might must of in on at to for from by with without about into over under
between among through during before after above below up down out off again
further once here there when where why how all any both each few more most other
some such no nor not only own same so too very s t just don now also it its as we
you your they their them he she his her him i me my our us what which who whom
whose explain describe tell list compare define give""".split()
)


def _content_tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if token not in _STOPWORDS and len(token) > 2
    }


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split(strip_code(text))
    out: list[str] = []
    for part in parts:
        cleaned = " ".join(part.split()).strip()
        # Drop the citation markers from the *measured* text; they are re-added
        # explicitly so we control where they land.
        cleaned = CITATION_RE.sub("", cleaned).strip()
        if len(cleaned) >= 40:
            out.append(cleaned)
    return out


def build_extractive_answer(
    question: str,
    excerpts: list[dict[str, Any]],
    *,
    max_sentences: int = 6,
    max_chars: int = 2200,
) -> tuple[str, dict[str, Any]]:
    """Compose an evidence-only answer.

    `excerpts` are numbered evidence records: {"number", "label", "content", ...}

    Returns (markdown_text, metadata). The metadata records exactly which excerpts
    contributed, so the UI can still show real source cards.
    """
    if not excerpts:
        return (
            "**Offline extractive answer**\n\n"
            "No evidence was retrieved from this workspace, so there is nothing to quote. "
            "No language model was used, and no online service was contacted.",
            {"sentences": 0, "excerpts_used": [], "mode": "extractive"},
        )

    question_tokens = _content_tokens(question)

    candidates: list[tuple[float, int, int, str]] = []

    for position, excerpt in enumerate(excerpts):
        number = int(excerpt.get("number", position + 1))
        for sentence_index, sentence in enumerate(_split_sentences(str(excerpt.get("content", "")))):
            sentence_tokens = _content_tokens(sentence)
            if not sentence_tokens:
                continue
            overlap = len(question_tokens & sentence_tokens) / len(question_tokens or {1})
            # Slight preference for earlier excerpts, which scored higher in retrieval.
            score = overlap + (0.15 / (position + 1))
            candidates.append((score, number, sentence_index, sentence))

    if not candidates:
        return (
            "**Offline extractive answer**\n\n"
            "The retrieved excerpts did not contain any sentences that could be matched to "
            "this question. No language model was used, and no online service was contacted.",
            {"sentences": 0, "excerpts_used": [], "mode": "extractive"},
        )

    candidates.sort(key=lambda item: item[0], reverse=True)

    selected: list[tuple[int, int, str]] = []
    used_numbers: list[int] = []
    total_chars = 0
    seen: set[str] = set()

    for score, number, sentence_index, sentence in candidates:
        if score <= 0:
            continue
        fingerprint = sentence[:120].lower()
        if fingerprint in seen:
            continue
        if total_chars + len(sentence) > max_chars:
            continue
        seen.add(fingerprint)
        selected.append((number, sentence_index, sentence))
        if number not in used_numbers:
            used_numbers.append(number)
        total_chars += len(sentence)
        if len(selected) >= max_sentences:
            break

    if not selected:
        return (
            "**Offline extractive answer**\n\n"
            "The retrieved excerpts did not share enough wording with this question to "
            "produce a quoted answer. No language model was used, and no online service "
            "was contacted.",
            {"sentences": 0, "excerpts_used": [], "mode": "extractive"},
        )

    # Keep the answer in a sensible reading order: by excerpt, then by position.
    selected.sort(key=lambda item: (item[0], item[1]))

    lines: list[str] = [
        "> **Offline extractive answer.** The local language model is unavailable, so this "
        "answer quotes the most relevant sentences directly from your documents instead of "
        "generating new prose. No online service was contacted.",
        "",
    ]

    current_number: int | None = None
    for number, _, sentence in selected:
        if number != current_number:
            label = next(
                (e.get("label", "") for e in excerpts if int(e.get("number", -1)) == number),
                "",
            )
            lines.append(f"**From [{number}] {label}**")
            lines.append("")
            current_number = number
        lines.append(f"- {sentence} [{number}]")

    lines.append("")
    lines.append(
        "_These are verbatim quotations from the retrieved evidence, not generated text. "
        "Grounding is therefore inherent: every sentence is a direct citation._"
    )

    metadata = {
        "mode": "extractive",
        "sentences": len(selected),
        "excerpts_used": used_numbers,
        "characters": total_chars,
        "used_language_model": False,
        "contacted_online_service": False,
    }

    return "\n".join(lines), metadata
