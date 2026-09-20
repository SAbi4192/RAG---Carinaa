"""
Shared validation for presentation transforms.

WHY VALIDATE AT ALL
-------------------
Translation and shortening are the two places where a language model touches an
already-grounded answer. That is exactly where meaning can silently drift:

  * a translator drops a citation because it "looked like noise"
  * a shortener quietly deletes a caveat, or a number, or a whole concept

Neither of those is acceptable, and neither is visible to the user unless we check
for it. So we do not trust the model's output - we compare it against the original
and report what changed.

WHAT WE CHECK
-------------
  citation_set_preserved   every [n] in the source still appears in the output
  numbers_preserved        every number in the source still appears
  no_invented_citations    the output cites nothing that was not in the source
  concept_coverage         how much of the source's vocabulary survived
  length_ratio             output length / source length

WHAT WE DO NOT CLAIM
--------------------
Lexical checks cannot prove semantic equivalence. A translation into Tamil will
share almost no vocabulary with the English source, so `concept_coverage` is
meaningless for translation and we do not gate on it there. We gate on the things
we CAN verify - citations, numbers, and invented content - and we surface the rest
as information rather than as a pass/fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.rag.citations import (
    WEB_CITATION_RE,
    extract_citation_numbers,
    strip_code,
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# Bounds on len(translation) / len(source). See the note in validate_translation:
# these detect degeneration and dropped content, not style. A faithful Tamil
# translation of English lands near 1.7x, so 3.0x is already implausible.
MAX_TRANSLATION_LENGTH_RATIO = 3.0
MIN_TRANSLATION_LENGTH_RATIO = 0.30

_STOPWORDS = frozenset(
    """a an the and or but if then than that this these those is are was were be been
being am do does did doing have has had having will would shall should can could
may might must of in on at to for from by with without about into over under
between among through during before after above below up down out off again
further once here there when where why how all any both each few more most other
some such no nor not only own same so too very s t just don now also it its as we
you your they their them he she his her him i me my our us""".split()
)


@dataclass
class TransformValidation:
    """The result of comparing a transformed answer against its source."""

    passed: bool = True
    source_citations: list[int] = field(default_factory=list)
    output_citations: list[int] = field(default_factory=list)
    missing_citations: list[int] = field(default_factory=list)
    invented_citations: list[int] = field(default_factory=list)

    source_numbers: list[str] = field(default_factory=list)
    missing_numbers: list[str] = field(default_factory=list)

    concept_coverage: float = 0.0
    length_ratio: float = 0.0

    issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "source_citations": self.source_citations,
            "output_citations": self.output_citations,
            "missing_citations": self.missing_citations,
            "invented_citations": self.invented_citations,
            "source_numbers": self.source_numbers,
            "missing_numbers": self.missing_numbers,
            "concept_coverage": round(self.concept_coverage, 4),
            "length_ratio": round(self.length_ratio, 4),
            "issues": self.issues,
            "notes": self.notes,
        }


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(strip_code(text).lower())
        if token not in _STOPWORDS and len(token) > 2
    }


def _citation_numbers(text: str) -> list[int]:
    # Goes through the shared extractor so a grouped marker `[2, 4]` counts as two
    # citations. Reading it as one - or missing it entirely - would let a
    # translation drop half its sources and still pass validation.
    return sorted(set(extract_citation_numbers(text)))


def _web_citation_numbers(text: str) -> list[int]:
    return sorted({int(m.group(1)) for m in WEB_CITATION_RE.finditer(strip_code(text))})


def _numbers(text: str) -> list[str]:
    # Normalise "1,200" -> "1200" so formatting differences are not flagged.
    return sorted({m.replace(",", "") for m in _NUMBER_RE.findall(strip_code(text))})


def validate_translation(source: str, output: str) -> TransformValidation:
    """Validate a translated answer.

    Gates on citations and numbers - the two things a translation must carry across
    unchanged - and on length plausibility. Vocabulary overlap is reported but not
    enforced, because a Tamil or Japanese translation legitimately shares no Latin
    words with its English source.
    """
    result = TransformValidation()

    result.source_citations = _citation_numbers(source) + _web_citation_numbers(source)
    result.output_citations = _citation_numbers(output) + _web_citation_numbers(output)

    result.missing_citations = sorted(set(result.source_citations) - set(result.output_citations))
    result.invented_citations = sorted(set(result.output_citations) - set(result.source_citations))

    result.source_numbers = _numbers(source)
    result.missing_numbers = sorted(set(result.source_numbers) - set(_numbers(output)))

    result.concept_coverage = 0.0  # not meaningful across languages
    result.length_ratio = round(len(output) / max(1, len(source)), 4)
    result.notes.append(
        "Vocabulary overlap is not enforced for translation: a correct translation into "
        "another script shares no words with the source."
    )

    if result.missing_citations:
        result.passed = False
        result.issues.append(
            f"The translation dropped citation(s) {result.missing_citations} that were "
            f"present in the original."
        )

    if result.invented_citations:
        result.passed = False
        result.issues.append(
            f"The translation introduced citation(s) {result.invented_citations} that do "
            f"not exist in the original."
        )

    if result.missing_numbers:
        result.passed = False
        result.issues.append(
            f"Number(s) {result.missing_numbers[:8]} from the original are missing from "
            f"the translation."
        )

    if len(output.strip()) < 20:
        result.passed = False
        result.issues.append("The translation is suspiciously short.")

    # ---- length plausibility -------------------------------------------
    # A translation carries the same content as its source, so its length is
    # bounded from both sides. This is a DEGENERATION DETECTOR, not a style rule.
    #
    # The local 3B model did exactly this: translating a 552-character answer into
    # Tamil produced 2082 characters - a 3.8x expansion - mixing English words into
    # Tamil and looping. Every citation and number still matched, so the checks
    # above all passed and the gibberish would have been shown to the user.
    #
    # The bounds are deliberately loose: a faithful Tamil translation lands near
    # 1.7x, so 3.0x is already far past anything legitimate.
    if result.length_ratio > MAX_TRANSLATION_LENGTH_RATIO:
        result.passed = False
        result.issues.append(
            f"The translation is {result.length_ratio:.1f}x the length of the original, "
            f"which means the model repeated itself instead of translating."
        )
    elif len(source.strip()) >= 200 and result.length_ratio < MIN_TRANSLATION_LENGTH_RATIO:
        result.passed = False
        result.issues.append(
            f"The translation is only {result.length_ratio:.2f}x the length of the "
            f"original, which means part of the answer was dropped."
        )

    return result


def validate_shortening(
    source: str,
    output: str,
    *,
    level: str = "short",
    min_concept_coverage: float = 0.55,
) -> TransformValidation:
    """Validate a shortened answer.

    Gates on three things:
      * citations preserved (a retained claim keeps its citation)
      * numbers preserved (the highest-stakes tokens in a grounded answer)
      * concept coverage above a floor (we did not throw away the meaning)

    The coverage floor is deliberately generous. Compression legitimately removes
    words; what it must not remove is *concepts*. If coverage collapses, the
    shortening went too far and we reject it rather than show the user a version
    that quietly means something different.
    """
    result = TransformValidation()

    result.source_citations = _citation_numbers(source) + _web_citation_numbers(source)
    result.output_citations = _citation_numbers(output) + _web_citation_numbers(output)

    result.missing_citations = sorted(set(result.source_citations) - set(result.output_citations))
    result.invented_citations = sorted(set(result.output_citations) - set(result.source_citations))

    source_tokens = _tokens(source)
    output_tokens = _tokens(output)
    result.concept_coverage = (
        len(source_tokens & output_tokens) / len(source_tokens) if source_tokens else 1.0
    )

    result.source_numbers = _numbers(source)
    result.missing_numbers = sorted(set(result.source_numbers) - set(_numbers(output)))

    result.length_ratio = round(len(output) / max(1, len(source)), 4)

    if result.missing_citations:
        result.passed = False
        result.issues.append(
            f"The shortened version dropped citation(s) {result.missing_citations}. A "
            f"retained claim must keep its citation."
        )

    if result.invented_citations:
        result.passed = False
        result.issues.append(
            f"The shortened version introduced citation(s) {result.invented_citations} that "
            f"were not in the original."
        )

    if result.missing_numbers:
        result.passed = False
        result.issues.append(
            f"Number(s) {result.missing_numbers[:8]} from the original were lost."
        )

    if result.concept_coverage < min_concept_coverage:
        result.passed = False
        result.issues.append(
            f"Only {result.concept_coverage:.0%} of the original's key vocabulary survived, "
            f"which is below the {min_concept_coverage:.0%} floor for a safe compression."
        )

    if result.length_ratio >= 1.0:
        result.passed = False
        result.issues.append(
            "The shortened version is not shorter than the original."
        )

    if level == "very_short" and result.length_ratio > 0.75:
        result.notes.append(
            "Requested a very short version, but the result is still close to the "
            "original length."
        )

    return result
