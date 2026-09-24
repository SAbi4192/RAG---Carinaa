"""
Grounding.

WHAT GROUNDING IS, AND WHAT IT IS NOT
-------------------------------------
Grounding checks whether the generated answer is actually SUPPORTED by the evidence
that was retrieved. It is the difference between:

  "the model wrote something that sounds right"
and
  "the model wrote something we can point at a source for".

What grounding gives you: a reduction in hallucination risk, and an honest signal
to the user when the system is unsure.

What grounding does NOT give you: a guarantee of factual correctness. An answer can
be perfectly grounded in a document that is itself wrong. Grounding measures the
relationship between the answer and the evidence - nothing more. We never claim
otherwise in the UI.

THE FOUR OUTCOMES (spec section 20)
-----------------------------------
  SUPPORTED              every claim is cited, every citation is valid, and the
                         cited text overlaps the claim it supports
  PARTIALLY_SUPPORTED    citations are valid, but some claims are uncited or weakly
                         supported by the excerpt they point at
  INSUFFICIENT_EVIDENCE  retrieval found nothing useful, or the model correctly
                         refused because the documents do not cover the question
  CITATION_ERROR         the answer cites something that does not exist in the
                         retrieved set - a fabricated citation

HOW EVIDENCE SUPPORT IS MEASURED
--------------------------------
Lexical overlap, deliberately. For each sentence that carries a citation, we take
the content words of the sentence, the content words of the cited excerpt, and
measure how much of the sentence is covered by the excerpt.

We use lexical overlap rather than a second LLM call because:
  * it is deterministic - the same answer always gets the same verdict, which
    matters when you are demonstrating the system to an examiner
  * it is instant and free
  * it is explainable - you can show the instructor the actual word sets
  * it does not require a network call, so it works in Offline mode

The trade-off is that a correct paraphrase with no shared vocabulary can be scored
as weakly supported. We therefore treat a weak score as "partially supported"
rather than "unsupported", and we surface the raw number so the user can judge.
`docs/07_grounding.md` explains how to swap in an NLI-based checker if the team
wants higher precision later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.rag.citations import (
    CitationReport,
    count_citation_occurrences,
    strip_code,
)

# ---------------------------------------------------------------------------
# Statuses
# ---------------------------------------------------------------------------
STATUS_SUPPORTED = "SUPPORTED"
STATUS_PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
STATUS_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
STATUS_CITATION_ERROR = "CITATION_ERROR"

STATUS_LABELS: dict[str, str] = {
    STATUS_SUPPORTED: "Supported by the retrieved evidence",
    STATUS_PARTIALLY_SUPPORTED: "Partially supported by the retrieved evidence",
    STATUS_INSUFFICIENT_EVIDENCE: "Not enough evidence in the documents",
    STATUS_CITATION_ERROR: "Citation problem detected",
}

STATUS_TONE: dict[str, str] = {
    STATUS_SUPPORTED: "positive",
    STATUS_PARTIALLY_SUPPORTED: "caution",
    STATUS_INSUFFICIENT_EVIDENCE: "neutral",
    STATUS_CITATION_ERROR: "negative",
}

# ---------------------------------------------------------------------------
# Refusal detection
# ---------------------------------------------------------------------------
REFUSAL_MARKERS: tuple[str, ...] = (
    "could not find this in the documents",
    "could not find that in the documents",
    "not mentioned in the provided",
    "no information about",
    "the documents do not contain",
    "the documents do not mention",
    "the provided documents do not",
    "i don't have enough information",
    "i do not have enough information",
    "not covered in the documents",
    "cannot be answered from the documents",
    "no relevant information was found",
    "the context does not contain",
    "the context does not mention",
    "insufficient information",
)

# ---------------------------------------------------------------------------
# Sentence splitting / tokenisation
# ---------------------------------------------------------------------------
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|\n{2,}")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")
_NUMBER_RE = re.compile(r"\d[\d,.]*")

# A compact English stop-word list. We only need it for the overlap metric, so it
# stays short and readable rather than importing a 400-entry dependency.
_STOPWORDS = frozenset(
    """a an the and or but if then than that this these those is are was were be been
being am do does did doing have has had having will would shall should can could
may might must of in on at to for from by with without about into over under
between among through during before after above below up down out off again
further once here there when where why how all any both each few more most other
some such no nor not only own same so too very s t just don now also it its it's
as we you your they their them he she his her him i me my our us""".split()
)

_MIN_SENTENCE_CHARS = 25


@dataclass
class SentenceVerdict:
    """Per-sentence grounding result. Shown in Developer Mode."""

    text: str
    citations: list[int]
    support: float
    verdict: str  # supported | weak | uncited
    best_excerpt: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text[:400],
            "citations": self.citations,
            "support": round(self.support, 4),
            "verdict": self.verdict,
            "best_excerpt": self.best_excerpt,
        }


@dataclass
class GroundingResult:
    """Outcome of a grounding check."""

    status: str = STATUS_INSUFFICIENT_EVIDENCE
    refused: bool = False
    reason: str = ""

    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    sentences: list[SentenceVerdict] = field(default_factory=list)

    supported_count: int = 0
    weak_count: int = 0
    uncited_count: int = 0

    cited_numbers: list[int] = field(default_factory=list)
    unused_numbers: list[int] = field(default_factory=list)
    invalid_numbers: list[int] = field(default_factory=list)

    citation_count: int = 0
    excerpt_count: int = 0
    top_score: float = 0.0

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def tone(self) -> str:
        return STATUS_TONE.get(self.status, "neutral")

    @property
    def is_supported(self) -> bool:
        return self.status == STATUS_SUPPORTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "label": self.label,
            "tone": self.tone,
            "reason": self.reason,
            "refused": self.refused,
            "checks": self.checks,
            "sentences": [s.as_dict() for s in self.sentences],
            "counts": {
                "supported": self.supported_count,
                "weak": self.weak_count,
                "uncited": self.uncited_count,
            },
            "cited_numbers": self.cited_numbers,
            "unused_numbers": self.unused_numbers,
            "invalid_numbers": self.invalid_numbers,
            "citation_count": self.citation_count,
            "excerpt_count": self.excerpt_count,
            "top_score": round(self.top_score, 4),
            "disclaimer": (
                "Grounding measures whether this answer is supported by the retrieved "
                "evidence. It is not a guarantee that the underlying document is correct."
            ),
        }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def check_grounding(
    answer: str,
    excerpts: list[dict[str, Any]],
    citation_report: CitationReport,
    *,
    top_score: float = 0.0,
    score_scale: str = "cosine",
) -> GroundingResult:
    """Run every grounding check and produce a single honest verdict.

    `score_scale` names the scale `top_score` is on (cosine for dense, bm25 for
    keyword-only, rrf for hybrid). The sufficiency text must label the number
    with its REAL scale: under bm25 or hybrid the top score is not a cosine
    similarity at all, and calling a BM25 value "cosine similarity" is the exact
    mislabeled-scale failure the rest of the pipeline works to prevent.
    """
    scale_label = {
        "cosine": "cosine similarity",
        "bm25": "BM25 score",
        "rrf": "reciprocal-rank score",
    }.get((score_scale or "cosine").lower(), "score")
    result = GroundingResult(
        excerpt_count=len(excerpts),
        citation_count=count_citation_occurrences(answer),
        cited_numbers=list(citation_report.cited_numbers),
        unused_numbers=list(citation_report.unused_numbers),
        invalid_numbers=list(citation_report.invalid_numbers),
        top_score=top_score,
    )

    refused = is_refusal(answer)
    result.refused = refused

    # =====================================================================
    # Check 1 - retrieval sufficiency
    # =====================================================================
    sufficient = len(excerpts) > 0
    result.checks["retrieval_sufficiency"] = {
        "passed": sufficient,
        "detail": (
            f"{len(excerpts)} excerpt(s) were retrieved; the best {scale_label} was "
            f"{top_score:.3f}."
            if sufficient
            else "No excerpts were retrieved from this workspace."
        ),
        "excerpt_count": len(excerpts),
        "top_score": round(top_score, 4),
    }

    if not sufficient:
        result.status = STATUS_INSUFFICIENT_EVIDENCE
        result.reason = (
            "Retrieval returned no candidates, so there was no evidence to ground an "
            "answer in."
        )
        return result

    # A correct refusal is a SUCCESS of the system, not a failure. The model was
    # given evidence, found it did not cover the question, and said so. That is
    # exactly the behaviour we want, and we label it INSUFFICIENT_EVIDENCE so the
    # UI can present it as a deliberate, correct outcome.
    if refused:
        result.status = STATUS_INSUFFICIENT_EVIDENCE
        result.reason = (
            "The model reported that the retrieved evidence does not answer this "
            "question. This is the intended behaviour when the workspace does not "
            "cover the topic."
        )
        result.checks["citation_validity"] = {
            "passed": not citation_report.invalid_numbers,
            "detail": (
                "The refusal contains no citations, as expected."
                if not citation_report.citations
                else "The refusal cited sources, which is unusual."
            ),
        }
        return result

    # =====================================================================
    # Check 2 - citation validity (fabricated citations)
    # =====================================================================
    validity_passed = not citation_report.invalid_numbers and not citation_report.errors
    result.checks["citation_validity"] = {
        "passed": validity_passed,
        "detail": (
            "Every citation resolves to an excerpt that was actually retrieved."
            if validity_passed
            else " ".join(citation_report.errors)
            or f"Invalid citation numbers: {citation_report.invalid_numbers}"
        ),
        "invalid_numbers": citation_report.invalid_numbers,
        "errors": citation_report.errors,
    }

    # =====================================================================
    # Check 3 - citation presence
    # =====================================================================
    sentences = _split_sentences(answer)
    claims = [s for s in sentences if _is_claim(s)]

    presence_passed = bool(citation_report.citations) or not claims
    result.checks["citation_presence"] = {
        "passed": presence_passed,
        "detail": (
            f"{len(citation_report.citations)} citation(s) across {len(claims)} claim "
            f"sentence(s)."
            if presence_passed
            else f"{len(claims)} claim sentence(s) carried no citation at all."
        ),
        "claim_sentences": len(claims),
        "citations": len(citation_report.citations),
    }

    # =====================================================================
    # Check 4 - evidence support (per sentence)
    # =====================================================================
    by_number = {int(e["number"]): e for e in excerpts if "number" in e}
    verdicts: list[SentenceVerdict] = []

    for sentence in claims:
        numbers = _sentence_citations(sentence)
        if not numbers:
            verdicts.append(
                SentenceVerdict(
                    text=sentence, citations=[], support=0.0, verdict="uncited"
                )
            )
            continue

        best_support = 0.0
        best_number: int | None = None
        for number in numbers:
            excerpt = by_number.get(number)
            if excerpt is None:
                continue
            score = _overlap(sentence, str(excerpt.get("content", "")))
            if score > best_support:
                best_support, best_number = score, number

        verdict = (
            "supported"
            if best_support >= settings.grounding_min_overlap
            else "weak"
        )
        verdicts.append(
            SentenceVerdict(
                text=sentence,
                citations=numbers,
                support=best_support,
                verdict=verdict,
                best_excerpt=best_number,
            )
        )

    result.sentences = verdicts
    result.supported_count = sum(1 for v in verdicts if v.verdict == "supported")
    result.weak_count = sum(1 for v in verdicts if v.verdict == "weak")
    result.uncited_count = sum(1 for v in verdicts if v.verdict == "uncited")

    total_claims = len(verdicts) or 1
    support_ratio = result.supported_count / total_claims

    result.checks["evidence_support"] = {
        "passed": result.weak_count == 0 and result.uncited_count == 0,
        "detail": (
            f"{result.supported_count}/{len(verdicts)} claim sentences share enough "
            f"vocabulary with the excerpt they cite (threshold "
            f"{settings.grounding_min_overlap:.2f})."
        ),
        "supported": result.supported_count,
        "weak": result.weak_count,
        "uncited": result.uncited_count,
        "threshold": settings.grounding_min_overlap,
    }

    # =====================================================================
    # Verdict
    # =====================================================================
    if not validity_passed:
        result.status = STATUS_CITATION_ERROR
        result.reason = (
            "The answer contains a citation that does not correspond to any retrieved "
            "excerpt. Treat the citations in this answer as unreliable."
        )
    elif result.supported_count == 0 and len(verdicts) > 0:
        result.status = STATUS_PARTIALLY_SUPPORTED
        result.reason = (
            "Citations are valid, but none of the claim sentences could be matched to "
            "their cited excerpt with confidence. The answer may be paraphrased beyond "
            "our lexical check, or it may not be fully supported."
        )
    elif result.weak_count or result.uncited_count or support_ratio < 1.0:
        result.status = STATUS_PARTIALLY_SUPPORTED
        parts = []
        if result.uncited_count:
            parts.append(f"{result.uncited_count} claim sentence(s) without a citation")
        if result.weak_count:
            parts.append(f"{result.weak_count} claim sentence(s) only weakly matching their source")
        result.reason = "Some claims need attention: " + "; ".join(parts) + "."
    else:
        result.status = STATUS_SUPPORTED
        result.reason = (
            "Every claim sentence cites a valid excerpt that shares substantial "
            "vocabulary with it."
        )

    # Requirement override: if the deployment demands citations and the answer has
    # none, we cannot call it supported no matter how good the evidence was.
    if settings.require_citations and not citation_report.citations and claims:
        result.status = STATUS_PARTIALLY_SUPPORTED
        result.reason = (
            "This deployment requires citations, and the answer did not include any."
        )

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def is_refusal(answer: str) -> bool:
    """Did the model decline to answer for lack of evidence?"""
    text = strip_code(answer).lower().strip()
    if not text:
        return True
    if any(marker in text for marker in REFUSAL_MARKERS):
        return True
    # Very short answers with no citations and no numbers are almost always refusals.
    if len(text) < 180 and not count_citation_occurrences(answer) and "?" not in text:
        return any(
            phrase in text
            for phrase in ("not in the", "no information", "cannot", "unable to", "don't know")
        )
    return False


def _split_sentences(text: str) -> list[str]:
    plain = strip_code(text)
    plain = re.sub(r"^\s*[-*+]\s+", "", plain, flags=re.MULTILINE)
    parts = _SENTENCE_SPLIT.split(plain)
    out: list[str] = []
    for part in parts:
        cleaned = " ".join(part.split()).strip()
        if len(cleaned) >= _MIN_SENTENCE_CHARS:
            out.append(cleaned)
    return out


def _is_claim(sentence: str) -> bool:
    """Is this sentence a factual claim we should require support for?

    Questions, bare headings and list labels are not claims.
    """
    stripped = sentence.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return False
    if stripped.endswith(":") and len(stripped) < 80:
        return False
    # Needs at least one content word.
    return bool(_content_tokens(stripped))


def _sentence_citations(sentence: str) -> list[int]:
    # Shared extractor, so a grouped marker `[2, 4]` supports the sentence with
    # BOTH sources rather than being read as a single unknown citation.
    from app.rag.citations import extract_citation_numbers

    return sorted(set(extract_citation_numbers(sentence)))


def _content_tokens(text: str) -> set[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    return {_stem(t) for t in tokens if t not in _STOPWORDS and len(t) > 2}


def _stem(token: str) -> str:
    """Crude suffix stripping so 'utilization' and 'utilize' partially match.

    Deliberately simple. A real stemmer would add a dependency and make the metric
    harder to explain to an examiner; this captures the common plural/gerund cases
    and nothing more.
    """
    for suffix in ("ations", "ation", "ings", "ing", "ies", "ers", "er", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _overlap(sentence: str, excerpt: str) -> float:
    """Fraction of the sentence's content words that appear in the excerpt."""
    sentence_tokens = _content_tokens(sentence)
    if not sentence_tokens:
        return 0.0
    excerpt_tokens = _content_tokens(excerpt)
    if not excerpt_tokens:
        return 0.0

    covered = len(sentence_tokens & excerpt_tokens)

    # Numbers are the highest-stakes tokens in a grounded answer: if the sentence
    # says "12 months" and the excerpt says "24 months", the answer is wrong even
    # though the prose matches. Check them explicitly and penalise mismatches.
    sentence_numbers = set(_NUMBER_RE.findall(sentence))
    if sentence_numbers:
        excerpt_numbers = set(_NUMBER_RE.findall(excerpt))
        matched_numbers = sentence_numbers & excerpt_numbers
        number_ratio = len(matched_numbers) / len(sentence_numbers)
        # Weight: 70% vocabulary overlap, 30% numeric agreement.
        return 0.7 * (covered / len(sentence_tokens)) + 0.3 * number_ratio

    return covered / len(sentence_tokens)


def grounding_summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate grounding outcomes for the Analytics page. Real counts only."""
    counts: dict[str, int] = {status: 0 for status in STATUS_LABELS}
    refused = 0
    for row in history:
        status = str(row.get("grounding_status", ""))
        if status in counts:
            counts[status] += 1
        if row.get("refused"):
            refused += 1

    total = sum(counts.values()) or 1
    return {
        "total": sum(counts.values()),
        "counts": counts,
        "refusals": refused,
        "supported_rate": round(counts[STATUS_SUPPORTED] / total, 4),
        "citation_error_rate": round(counts[STATUS_CITATION_ERROR] / total, 4),
    }
