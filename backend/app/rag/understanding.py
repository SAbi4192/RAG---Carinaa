"""Query understanding: what is the user actually asking about?

Two problems this solves, both of which made Carinaa feel like a search box rather
than a conversation.

1. PAGE REFERENCES.
   "Tell me about the second page of the PDF" failed not because the page metadata
   was missing - it was already stored on every chunk - but because nothing ever
   looked for a page reference. Dense retrieval compares MEANING, and the words
   "second page" have no semantic relationship to whatever happens to be written on
   page 2. So the query was embedded, matched nothing useful, and the model correctly
   reported that the retrieved excerpts did not answer it.
   The fix is to recognise the reference and filter by metadata, not to hope the
   right page floats to the top.

2. FOLLOW-UP QUESTIONS.
   "What is my name?" and "explain the second one" are unanswerable in isolation.
   They need the conversation. This module decides whether a question depends on
   earlier turns, and if so produces a standalone version for retrieval.

WHY DETERMINISTIC FIRST
-----------------------
Page detection is a regex, not a model call. "page 12" means page 12 - there is
nothing to interpret, and a model call would add latency and a failure mode to
something that can simply be correct. The LLM is used only for the genuinely
ambiguous job: rewriting a follow-up into a standalone question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Page references
# ---------------------------------------------------------------------------
_ORDINALS: dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20,
}

_WORD_NUMBERS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}

# Order matters: the more specific patterns are tried first so "the second page"
# is not shadowed by a looser "page" match.
_PAGE_PATTERNS: list[tuple[str, str]] = [
    # "page 12", "pages 12", "p.12", "p 12", "page no 12"
    (r"\b(?:pages?|p\.?)\s*(?:no\.?|number)?\s*(\d{1,4})\b", "number"),
    # "the second page", "2nd page"
    (r"\b(\d{1,2})(?:st|nd|rd|th)\s+page\b", "ordinal_suffix"),
    # "the second page" / "the fifth page"
    (
        r"\b(" + "|".join(_ORDINALS) + r")\s+page\b",
        "ordinal_word",
    ),
    # "page two", "page five"
    (
        r"\b(?:pages?|p\.?)\s+(" + "|".join(_WORD_NUMBERS) + r")\b",
        "number_word",
    ),
]

_RELATIVE_PAGE = re.compile(
    r"\b(previous|last|next|following|current|same)\s+page\b", re.IGNORECASE
)

# Signals that a question cannot stand alone. Deliberately narrow.
#
# The asymmetry matters: rewriting an independent question into something the user
# did not ask is worse than failing to rewrite a follow-up, because the first sends
# retrieval after the wrong thing while the second merely retrieves less well. So the
# bar for "this is a follow-up" is set high, and length alone never qualifies.
_FOLLOWUP_CUES = re.compile(
    r"\b("
    # Pronouns and possessives that must point at something earlier.
    r"it|its|it's|that|this|these|those|they|them|their|he|she|him|her|his|hers|"
    r"my|our|your|mine|ours|yours|"
    # Determiners that imply a previous referent.
    r"the\s+(?:same|other|previous|next|above|below|former|latter)|"
    r"the\s+(?:first|second|third|fourth|fifth|sixth|last|other)\s+"
    r"(?:one|step|stage|point|item|thing|law|part|section|chapter)|"
    # Explicit requests to continue.
    r"what\s+about|how\s+about|tell\s+me\s+more|go\s+on|continue|"
    r"say\s+more|more\s+detail|expand\s+on|elaborate|"
    r"why\s+is\s+that|how\s+so|explain\s+that|"
    # Bare ordinals used elliptically: "the second one", "the third".
    r"the\s+(?:first|second|third|fourth|fifth|sixth|last)\b"
    r")\b",
    re.IGNORECASE,
)

# Elliptical openers: "and the third?", "but why?" - the question is a continuation
# by construction.
_ELLIPTICAL_OPENER = re.compile(r"^\s*(?:and|but|also|then|so)\b", re.IGNORECASE)

# A fragment this short is only treated as a follow-up when it also contains a
# determiner, so "Explain photosynthesis." stays standalone while "and the third?"
# does not. Three words, not six - the earlier value was the source of the false
# positives.
_SHORT_FRAGMENT_WORDS = 3
_FRAGMENT_DETERMINER = re.compile(r"\b(the|that|those|these|it|them|one|other)\b", re.IGNORECASE)


@dataclass
class QueryUnderstanding:
    """What the system decided the user meant, and why."""

    original: str
    resolved: str
    is_followup: bool = False
    rewrite_reason: str = ""
    page_number: int | None = None
    page_phrase: str = ""
    page_is_relative: bool = False
    relative_direction: str = ""
    document_hint: str = ""
    notes: list[str] = field(default_factory=list)

    def as_trace_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "original_question": self.original,
            "resolved_question": self.resolved,
            "is_followup": self.is_followup,
        }
        if self.rewrite_reason:
            data["rewrite_reason"] = self.rewrite_reason
        if self.page_number is not None:
            data["page_reference"] = self.page_phrase
            data["page_number"] = self.page_number
        if self.page_is_relative:
            data["relative_page"] = self.relative_direction
        if self.document_hint:
            data["document_hint"] = self.document_hint
        if self.notes:
            data["notes"] = self.notes
        return data


def detect_page_reference(question: str) -> tuple[int | None, str, bool, str]:
    """Find an explicit page reference.

    Returns (page_number, matched_phrase, is_relative, direction).

    Relative references ("the next page") are DETECTED but not resolved here: doing
    so needs the page the conversation is currently on, which this function does not
    know. It reports the direction and lets the caller resolve it, rather than
    guessing a number that might be wrong.
    """
    relative = _RELATIVE_PAGE.search(question)
    if relative:
        return None, relative.group(0), True, relative.group(1).lower()

    for pattern, kind in _PAGE_PATTERNS:
        match = re.search(pattern, question, re.IGNORECASE)
        if not match:
            continue
        raw = match.group(1).lower()
        if kind == "number":
            value = int(raw)
        elif kind == "ordinal_suffix":
            value = int(raw)
        elif kind == "ordinal_word":
            value = _ORDINALS.get(raw)
        else:
            value = _WORD_NUMBERS.get(raw)

        # A page number of 0 is not a page. Guard rather than let it through.
        if value and value > 0:
            return value, match.group(0), False, ""

    return None, "", False, ""


def needs_conversation_context(question: str, history_length: int) -> bool:
    """Whether this question probably depends on earlier turns.

    Conservative by design. A false positive rewrites a self-contained question into
    something the user did not ask; a false negative merely leaves a follow-up
    unresolved. The first is worse, so the bar for "this is a follow-up" is kept high.
    """
    if history_length == 0:
        return False

    text = question.strip()
    if not text:
        return False

    if _FOLLOWUP_CUES.search(text):
        return True

    if _ELLIPTICAL_OPENER.match(text):
        return True

    # A very short fragment, but only with a determiner pointing backwards.
    words = text.split()
    return len(words) <= _SHORT_FRAGMENT_WORDS and bool(_FRAGMENT_DETERMINER.search(text))


def build_rewrite_messages(question: str, history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Ask the model to make a follow-up standalone.

    The model is asked to output ONLY the rewritten question. It is explicitly
    allowed to return the question unchanged, because the honest answer for a
    self-contained question is to leave it alone - and forcing a change would
    corrupt perfectly good queries.
    """
    lines: list[str] = [
        "Rewrite the user's latest question so it can be understood on its own.",
        "",
        "Rules:",
        "- Use the conversation to resolve pronouns and references such as 'it',",
        "  'that', 'the second one'.",
        "- Keep the meaning identical. Do not answer the question.",
        "- Do not add information that is not implied by the conversation.",
        "- If the question is already standalone, return it unchanged.",
        "- Output ONLY the rewritten question. No quotes, no explanation.",
        "",
        "Conversation so far:",
    ]
    for turn in history:
        role = "User" if turn.get("role") == "user" else "Assistant"
        content = (turn.get("content") or "").strip().replace("\n", " ")
        lines.append(f"{role}: {content[:400]}")

    lines.extend(["", f"Latest question: {question.strip()}", "", "Rewritten question:"])

    return [
        {
            "role": "system",
            "content": (
                "You rewrite questions to be self-contained. You never answer them. "
                "You output only the rewritten question."
            ),
        },
        {"role": "user", "content": "\n".join(lines)},
    ]


def clean_rewrite(raw: str, original: str, max_chars: int = 400) -> str:
    """Sanitise a model's rewrite, falling back to the original when unusable.

    Small models sometimes answer instead of rewriting, or wrap the result in quotes
    and preamble. A rewrite that is empty, absurdly long, or clearly a refusal is
    discarded - retrieval with the original question is a normal outcome, not a
    failure, so there is no reason to accept a bad rewrite.
    """
    text = (raw or "").strip()

    # Strip the wrappers small models like to add.
    for prefix in ("Rewritten question:", "Rewritten:", "Question:"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix) :].strip()

    text = text.strip().strip('"').strip("'").strip()

    # Take the first non-empty line; a model that answered instead of rewriting
    # usually produces several.
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")

    if not first_line:
        return original
    if len(first_line) > max_chars:
        return original
    # An answer rather than a rewrite: too long relative to the question and ends
    # like prose.
    if len(first_line) > max(120, len(original) * 6):
        return original

    return first_line
