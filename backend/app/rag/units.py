"""Unit references: "the third unit", "Unit III", "unit 3".

THE FAILURE THIS FIXES
----------------------
Reported behaviour on a real document:

    "What is the name of UNIT III?"   -> worked
    "What does the third unit say?"   -> "Not enough evidence"
    "Summarise Unit III"              -> sometimes the wrong section

The cause is not a threshold and not weak retrieval. It is that **"third" and "III"
are the same concept in two different alphabets, and nothing connected them.**

The document's body contains the literal string "UNIT III", so a question that says
"UNIT III" matches it. A question that says "third unit" shares no tokens with it at
all, and embeddings cannot bridge an ordinal to a Roman numeral on their own - that is
a transformation, not a similarity.

WHY THIS IS A QUERY EXPANSION, NOT A METADATA FILTER
----------------------------------------------------
Page and section references become FILTERS because the metadata exists to filter on. But
section metadata is only as complete as the document's headings: this document has
sections for UNIT I, IV and V, and none for II or III. Filtering on "UNIT III" would
therefore find nothing, even though the text is present.

So a unit reference is expanded into its canonical form and added to the search text.
That works whether or not the heading was detected, because it searches the CONTENT for
the string the document actually uses.

If a matching section DOES exist, the caller may still filter on it - the expansion is
additive and never replaces the semantic query.
"""

from __future__ import annotations

import re

_ROMAN_UNITS: list[tuple[int, str]] = [
    (1, "I"), (2, "II"), (3, "III"), (4, "IV"), (5, "V"),
    (6, "VI"), (7, "VII"), (8, "VIII"), (9, "IX"), (10, "X"),
    (11, "XI"), (12, "XII"), (13, "XIII"), (14, "XIV"), (15, "XV"),
]

_ORDINAL_WORDS: dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
}

_NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15,
}

# Words for the structural containers people actually use. A document may call the same
# thing a unit, a module or a chapter; the reference is the same shape either way.
_CONTAINERS = r"(?:unit|module|chapter|part|section)"

_UNIT_PATTERNS: list[tuple[str, str]] = [
    # "unit III", "unit iv"
    (rf"\b{_CONTAINERS}\s+([ivx]{{1,5}})\b", "roman"),
    # "unit 3"
    (rf"\b{_CONTAINERS}\s+(\d{{1,2}})\b", "number"),
    # "third unit", "3rd unit"
    (rf"\b(\d{{1,2}})(?:st|nd|rd|th)\s+{_CONTAINERS}\b", "number"),
    # "third unit", "third chapter"
    (rf"\b(" + "|".join(_ORDINAL_WORDS) + rf")\s+{_CONTAINERS}\b", "ordinal_word"),
    # "unit three"
    (rf"\b{_CONTAINERS}\s+(" + "|".join(_NUMBER_WORDS) + r")\b", "number_word"),
]

_ROMAN_VALUES = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
    "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12, "xiii": 13,
    "xiv": 14, "xv": 15,
}


def _to_roman(value: int) -> str:
    for number, roman in _ROMAN_UNITS:
        if number == value:
            return roman
    return ""


def detect_unit_reference(question: str) -> tuple[int | None, str, str]:
    """Find a unit/module/chapter reference.

    Returns (number, roman, matched_phrase).

    All three number forms are understood - Roman ("unit III"), Arabic ("unit 3") and
    word ("third unit", "unit three") - because people mix them freely and the whole
    point is to connect them to the single form the document uses.
    """
    for pattern, kind in _UNIT_PATTERNS:
        match = re.search(pattern, question, re.IGNORECASE)
        if not match:
            continue

        raw = match.group(1).lower()
        value: int | None = None

        if kind == "roman":
            value = _ROMAN_VALUES.get(raw)
        elif kind == "number":
            value = int(raw)
        elif kind == "ordinal_word":
            value = _ORDINAL_WORDS.get(raw)
        else:
            value = _NUMBER_WORDS.get(raw)

        if value and 1 <= value <= 15:
            return value, _to_roman(value), match.group(0)

    return None, "", ""


def expand_unit_reference(question: str) -> tuple[str, str]:
    """Return (expanded_question, note).

    The canonical form is appended rather than substituted, so the user's own wording
    still drives the semantic search. Appending adds the tokens the document actually
    uses ("UNIT III") without discarding the meaning the user expressed.

    The note is shown in the trace, so the expansion is inspectable rather than a silent
    edit to the question.
    """
    value, roman, phrase = detect_unit_reference(question)
    if not value or not roman:
        return question, ""

    # Do not duplicate a form the user already typed.
    if re.search(rf"\bunit\s+{roman}\b", question, re.IGNORECASE):
        return question, ""

    expanded = f"{question} (UNIT {roman})"
    return expanded, f'"{phrase}" resolved to "UNIT {roman}" and added to the search'


def _normalise(value: str) -> str:
    """Collapse punctuation and spacing, so 'UNIT – III' and 'UNIT III' compare equal.

    Real headings use en dashes, extra spaces and inconsistent casing. Comparing raw
    strings would only match the tidiest documents.
    """
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def match_unit_section(question: str, sections: list[str]) -> str | None:
    """The section whose heading names the unit the question asks about.

    This is the FIX that the lexical blend only approximated. The blend nudges scores;
    an index filter removes every chunk that is not in the right unit, which is what
    "don't return unrelated chunks" actually requires.

    Returns the exact stored section title, because that is what the metadata filter
    needs. Returns None when no heading names that unit - the caller then falls back to
    scoring, rather than filtering to nothing and losing the answer entirely.
    """
    _number, roman, _phrase = detect_unit_reference(question)
    if not roman:
        return None

    target = _normalise(f"UNIT {roman}")
    for section in sections:
        if target and target in _normalise(section):
            return section
    return None
