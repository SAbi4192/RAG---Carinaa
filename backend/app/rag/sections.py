"""Section references: "tell me about the Interviewing Techniques section".

Pages are a structural reference and so are sections, but they need different handling.
A page is a NUMBER, so it can be detected from the question alone. A section is a NAME
that exists only in the document, so the question has to be matched against the section
titles actually present in scope. That means the matching runs against real data, and a
question that names no existing section simply falls through to ordinary retrieval.

This is why section filtering cannot be done at query-understanding time in isolation -
it needs the workspace's section list. The vector store filters on the exact section
string, which the metadata already carries.
"""

from __future__ import annotations

import re

# Words too common to identify a section on their own.
_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with", "using",
    "section", "chapter", "part", "about", "tell", "me", "explain", "summarize",
    "summarise", "what", "does", "say", "cover", "covers", "discuss", "discusses",
    "please", "can", "you", "give", "more", "detail", "details", "info",
}


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (value or "").lower())


def _significant_words(value: str) -> list[str]:
    return [word for word in _normalise(value).split() if word and word not in _STOPWORDS]


def match_section(question: str, sections: list[str]) -> str | None:
    """The section the question names, if any.

    Matching is on SIGNIFICANT WORDS rather than the raw string, because section titles
    in a real document carry numbering and course codes - "1.3 Discovering areas of
    opportunity", "OME354 - APPLIED DESIGN THINKING" - that nobody types. Requiring an
    exact string match would mean the feature only worked for sections with tidy names.

    Returns the exact stored section title, because that is what the metadata filter
    needs. Returns None when nothing matches convincingly, so an ordinary question is
    never narrowed to a section by accident.
    """
    if not question or not sections:
        return None

    question_words = set(_significant_words(question))
    if not question_words:
        return None

    best: tuple[float, str] | None = None

    for section in sections:
        section_words = _significant_words(section)
        if not section_words:
            continue

        # How much of the section title the question actually contains.
        overlap = len(question_words & set(section_words))
        if overlap == 0:
            continue

        coverage = overlap / len(section_words)
        if coverage < 0.6:
            continue

        # Prefer the section with the highest coverage, then the most words matched -
        # so "Interviewing Techniques" beats a longer title that merely shares a word.
        score = (coverage, overlap)
        if best is None or score > best[0]:
            best = (score, section)

    return best[1] if best else None


def detect_relative_section(question: str) -> str:
    """Detect "the next section" / "the previous section" without resolving it.

    Same reasoning as relative pages: resolving needs the section the conversation is
    currently in, which the detector does not know. Reporting the direction is honest;
    inventing a section would not be.
    """
    match = re.search(
        r"\b(previous|last|next|following|current|same)\s+(?:section|chapter|part)\b",
        question,
        re.IGNORECASE,
    )
    return match.group(1).lower() if match else ""
