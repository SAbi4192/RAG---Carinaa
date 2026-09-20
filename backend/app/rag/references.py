"""Relative page references and ambiguous document references.

Two gaps left by the first pass at page awareness.

1. RELATIVE REFERENCES.
   "What about the next page?" was detected but never resolved, because resolving it
   needs the page the conversation is currently on - which the detector cannot know.
   This resolves it from the conversation: the most recent explicit page reference, or
   the pages the previous answer actually cited. Both are real recorded values; when
   neither exists the reference stays unresolved rather than being guessed.

2. AMBIGUITY.
   With two PDFs in scope, "tell me about the second page" has no single answer.
   Silently picking one would produce a confident answer from the wrong document, so
   the honest response is to ask which. A document named in the question - or
   established earlier in the conversation - removes the ambiguity.
"""

from __future__ import annotations

import re

from app.rag.understanding import detect_page_reference


def resolve_relative_page(
    direction: str,
    history: list[dict[str, str]],
    last_cited_pages: list[int] | None = None,
) -> tuple[int | None, str]:
    """Turn "the next page" into a number using what the conversation established.

    Returns (page_number, how_it_was_resolved). The second value is shown in the trace,
    so the reasoning is inspectable rather than magic.
    """
    base: int | None = None
    source = ""

    # Prefer the most recent explicit page reference in the conversation.
    for turn in reversed(history or []):
        if turn.get("role") != "user":
            continue
        number, _phrase, relative, _direction = detect_page_reference(turn.get("content") or "")
        if number is not None:
            base = number
            source = "the last page you asked about"
            break

    # Otherwise fall back to the pages the previous answer actually cited.
    if base is None and last_cited_pages:
        base = max(last_cited_pages)
        source = "the page the last answer came from"

    if base is None:
        return None, ""

    if direction in ("next", "following"):
        return base + 1, source
    if direction in ("previous", "last"):
        return base - 1, source
    if direction in ("current", "same"):
        return base, source
    return None, ""


def document_mentioned(question: str, filenames: list[str]) -> str | None:
    """Which in-scope document, if any, the question names.

    Matches on the full filename and on the stem, because people write "ADT_Notes"
    far more often than "ADT_Notes.pdf". Comparison is case-insensitive and ignores
    separators, so "adt notes" also matches.
    """
    def normalise(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    haystack = normalise(question)
    if not haystack:
        return None

    for filename in filenames:
        if normalise(filename) and normalise(filename) in haystack:
            return filename

    for filename in filenames:
        stem = filename.rsplit(".", 1)[0]
        if len(normalise(stem)) >= 4 and normalise(stem) in haystack:
            return filename

    return None


def needs_document_clarification(
    question: str,
    page_number: int | None,
    scope_filenames: list[str],
) -> list[str] | None:
    """Whether a page question is ambiguous across several documents.

    Returns the candidate filenames when the user must choose, or None when the
    question is answerable as asked.

    Only a PAGE reference triggers this. "Summarize this document" with two documents
    in scope is a different situation - it is reasonable to summarise both, whereas
    "page 2" has no meaning across two documents and picking one would be inventing
    an answer.
    """
    if page_number is None:
        return None
    if len(scope_filenames) <= 1:
        return None
    if document_mentioned(question, scope_filenames):
        return None
    return sorted(scope_filenames)
