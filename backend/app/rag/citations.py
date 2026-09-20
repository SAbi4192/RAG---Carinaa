"""
Citation resolution.

RETRIEVAL vs GROUNDING vs CITATIONS (spec section 20)
-----------------------------------------------------
These are three different jobs and it is worth keeping them straight:

  Retrieval  finds relevant evidence.
  Grounding  checks whether the generated answer is supported by that evidence.
  Citations  show the user WHERE the evidence came from.

This module does the third job. It parses the `[1]` markers out of the generated
answer, matches each one to the excerpt it refers to, and produces a structure the
UI can render as a clickable source card.

THE RULE THAT MATTERS MOST
--------------------------
A citation must point at evidence that was ACTUALLY RETRIEVED and ACTUALLY
SUPPORTED the claim. We therefore never trust the model's numbering. We check:

  * does the number exist in the retrieved set?      -> else CITATION_ERROR
  * was that excerpt actually used by the answer?    -> else it is "unused"

If the model cites [7] when only 5 excerpts were retrieved, that is a fabricated
citation. We detect it, mark the answer CITATION_ERROR, and surface it - we do not
silently drop the marker and pretend the answer was fine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

# A document citation marker. The model may group several sources into one
# bracket, and it does so constantly in practice:
#
#     [1]        [12]        [2, 4]        [1,2,3]
#
# An earlier version of this pattern matched only a lone number, `\[(\d{1,3})\]`.
# The effect was silent and serious: in the answer "...scale elastically [2, 4]"
# the group was invisible, so citations 2 and 4 were never resolved and the reader
# had no way to reach the evidence behind that claim. Grouping is normal citation
# style, so the parser has to understand it.
#
# The first digit is 1-9 because excerpt numbering is 1-based. That also keeps a
# prose reference like `arr[0]` - which `strip_code` cannot catch, since it is not
# inside backticks - from inventing a citation.
CITATION_RE = re.compile(r"\[\s*([1-9]\d{0,2}(?:\s*,\s*[1-9]\d{0,2})*)\s*\]")
# [W1] [W2]     - web citations
WEB_CITATION_RE = re.compile(r"\[W(\d{1,3})\]", re.IGNORECASE)

# Code fences must be excluded from citation parsing: a list like `arr[0]` in a
# code sample is not a citation, and treating it as one creates phantom sources.
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")

# Providers do not agree on what a citation bracket looks like. Gemini emits the
# ASCII form "[1]"; the Groq fallback has been observed emitting the full-width CJK
# form "【1】". That difference used to silently disable the entire citation and
# grounding pipeline - a well-cited answer was reported as having no citations at
# all, and grounding was downgraded to PARTIALLY_SUPPORTED.
#
# This is a fixed translation table rather than `unicodedata.normalize("NFKC")`
# for two reasons: NFKC does not actually fold "【" (U+3010), so it would not fix
# the observed case; and NFKC rewrites unrelated characters, which is unacceptable
# for text the user reads.
_BRACKET_VARIANTS = str.maketrans(
    {
        "【": "[", "】": "]",
        "［": "[", "］": "]",
        "〔": "[", "〕": "]",
        "〖": "[", "〗": "]",
        "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
        "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
    }
)


def normalize_citation_markers(text: str) -> str:
    """Rewrite look-alike citation brackets to canonical ASCII form.

    Idempotent, and safe on any text: it only touches bracket and digit look-alikes.
    """
    return text.translate(_BRACKET_VARIANTS) if text else text


@dataclass
class Citation:
    """One resolved source reference."""

    number: int
    marker: str
    kind: str = "document"  # document | web

    # Document citations
    chunk_id: int | None = None
    vector_id: str = ""
    document_id: int | None = None
    document_name: str = ""
    file_type: str = ""
    chunk_index: int | None = None

    # Provenance shown on the source card
    page_number: int | None = None
    page_end: int | None = None
    slide_number: int | None = None
    sheet_name: str | None = None
    section: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    json_path: str | None = None

    snippet: str = ""
    relevance: float = 0.0
    rank: int = 0

    # Web citations
    url: str = ""
    title: str = ""

    def location_label(self) -> str:
        """Human-readable location, e.g. 'Cloud_Computing.pdf · p.32'."""
        if self.kind == "web":
            return self.title or self.url or "Web result"

        parts: list[str] = [self.document_name or f"Document {self.document_id}"]

        if self.page_number:
            if self.page_end and self.page_end != self.page_number:
                parts.append(f"p.{self.page_number}-{self.page_end}")
            else:
                parts.append(f"p.{self.page_number}")
        elif self.slide_number:
            parts.append(f"slide {self.slide_number}")
        elif self.sheet_name:
            if self.row_start and self.row_end and self.row_end != self.row_start:
                parts.append(f"{self.sheet_name}, rows {self.row_start}-{self.row_end}")
            elif self.row_start:
                parts.append(f"{self.sheet_name}, row {self.row_start}")
            else:
                parts.append(str(self.sheet_name))
        elif self.json_path:
            parts.append(self.json_path)

        if self.section and self.section not in parts:
            parts.append(self.section)

        return " · ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "marker": self.marker,
            "kind": self.kind,
            "location_label": self.location_label(),
            "document_id": self.document_id,
            "document_name": self.document_name,
            "file_type": self.file_type,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "page_number": self.page_number,
            "page_end": self.page_end,
            "slide_number": self.slide_number,
            "sheet_name": self.sheet_name,
            "section": self.section,
            "row_start": self.row_start,
            "row_end": self.row_end,
            "json_path": self.json_path,
            "snippet": self.snippet,
            "relevance": round(self.relevance, 4),
            "rank": self.rank,
            "url": self.url,
            "title": self.title,
        }


@dataclass
class CitationReport:
    """Outcome of resolving citations for one answer."""

    citations: list[Citation] = field(default_factory=list)
    cited_numbers: list[int] = field(default_factory=list)
    web_cited_numbers: list[int] = field(default_factory=list)
    invalid_numbers: list[int] = field(default_factory=list)
    unused_numbers: list[int] = field(default_factory=list)
    has_citations: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.invalid_numbers and not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "has_citations": self.has_citations,
            "count": len(self.citations),
            "cited_numbers": self.cited_numbers,
            "web_cited_numbers": self.web_cited_numbers,
            "invalid_numbers": self.invalid_numbers,
            "unused_numbers": self.unused_numbers,
            "valid": self.valid,
            "errors": self.errors,
            "citations": [c.as_dict() for c in self.citations],
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def strip_code(text: str) -> str:
    """Remove code fences and inline code so `arr[0]` is not read as a citation."""
    without_fences = _CODE_FENCE_RE.sub(" ", text)
    return _INLINE_CODE_RE.sub(" ", without_fences)


def iter_citation_numbers(text: str) -> Iterator[int]:
    """Yield every document citation number in `text`, in order, keeping repeats.

    A grouped marker is flattened: `[2, 4]` yields 2 then 4. This is the single
    place that knows how to read a marker, so every caller - resolution, grounding
    and validation - sees the same set of citations.
    """
    normalized = normalize_citation_markers(text)
    for match in CITATION_RE.finditer(strip_code(normalized)):
        for part in match.group(1).split(","):
            part = part.strip()
            if part.isdigit():
                yield int(part)


def extract_citation_numbers(text: str) -> list[int]:
    """Ordered, de-duplicated document citation numbers, in order of first use."""
    seen: dict[int, None] = {}
    for number in iter_citation_numbers(text):
        seen.setdefault(number, None)
    return list(seen)


def extract_web_citation_numbers(text: str) -> list[int]:
    seen: dict[int, None] = {}
    normalized = normalize_citation_markers(text)
    for match in WEB_CITATION_RE.finditer(strip_code(normalized)):
        seen.setdefault(int(match.group(1)), None)
    return list(seen)


def count_citation_occurrences(text: str) -> int:
    """How many citation references the text makes.

    Counted per number, not per bracket, so `[2, 4]` counts as two - it points at
    two separate pieces of evidence.
    """
    return sum(1 for _ in iter_citation_numbers(text)) + len(
        WEB_CITATION_RE.findall(strip_code(normalize_citation_markers(text)))
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def resolve_citations(
    answer: str,
    excerpts: list[dict[str, Any]],
    *,
    web_sources: list[dict[str, Any]] | None = None,
    snippet_chars: int = 320,
) -> CitationReport:
    """Map the `[n]` markers in `answer` onto real retrieved excerpts.

    `excerpts` are the numbered evidence records built by the context builder:
        {"number": 1, "chunk_id": 42, "document_id": 7, "content": "...", ...}
    """
    report = CitationReport()
    by_number: dict[int, dict[str, Any]] = {
        int(e["number"]): e for e in excerpts if "number" in e
    }

    cited = extract_citation_numbers(answer)
    web_cited = extract_web_citation_numbers(answer)
    report.cited_numbers = cited
    report.web_cited_numbers = web_cited
    report.has_citations = bool(cited or web_cited)

    # ---- document citations -------------------------------------------
    for number in cited:
        excerpt = by_number.get(number)
        if excerpt is None:
            report.invalid_numbers.append(number)
            report.errors.append(
                f"The answer cited [{number}], but only {len(by_number)} excerpt(s) were "
                f"available to it. That citation is fabricated."
            )
            continue
        report.citations.append(_citation_from_excerpt(number, excerpt, snippet_chars))

    # ---- web citations --------------------------------------------------
    if web_sources:
        web_by_number = {int(s["number"]): s for s in web_sources if "number" in s}
        for number in web_cited:
            source = web_by_number.get(number)
            if source is None:
                report.invalid_numbers.append(number)
                report.errors.append(
                    f"The answer cited [W{number}], but no web result with that number existed."
                )
                continue
            report.citations.append(
                Citation(
                    number=number,
                    marker=f"[W{number}]",
                    kind="web",
                    url=str(source.get("url", "")),
                    title=str(source.get("title", "")),
                    snippet=str(source.get("snippet", ""))[:snippet_chars],
                )
            )
    elif web_cited:
        report.invalid_numbers.extend(web_cited)
        report.errors.append("The answer cited web results, but web search was not enabled.")

    # ---- unused evidence -----------------------------------------------
    report.unused_numbers = sorted(set(by_number) - set(cited))

    return report


def _citation_from_excerpt(number: int, excerpt: dict[str, Any], snippet_chars: int) -> Citation:
    metadata = excerpt.get("metadata") or {}
    content = str(excerpt.get("content", ""))

    return Citation(
        number=number,
        marker=f"[{number}]",
        kind="document",
        chunk_id=excerpt.get("chunk_id"),
        vector_id=str(excerpt.get("vector_id", "")),
        document_id=excerpt.get("document_id"),
        document_name=str(excerpt.get("document_name", "")),
        file_type=str(excerpt.get("file_type", "")),
        chunk_index=excerpt.get("chunk_index"),
        page_number=_as_int(metadata.get("page_number")),
        page_end=_as_int(metadata.get("page_end")),
        slide_number=_as_int(metadata.get("slide_number")),
        sheet_name=_as_str(metadata.get("sheet_name")),
        section=_as_str(metadata.get("section")),
        row_start=_as_int(metadata.get("row_start")),
        row_end=_as_int(metadata.get("row_end")),
        json_path=_as_str(metadata.get("json_path")),
        snippet=content[:snippet_chars] + ("..." if len(content) > snippet_chars else ""),
        relevance=float(excerpt.get("score", 0.0)),
        rank=int(excerpt.get("rank", 0)),
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


# ---------------------------------------------------------------------------
# Text transforms
# ---------------------------------------------------------------------------
def strip_citations_for_speech(text: str) -> str:
    """Remove citation markers so Read Aloud does not say "bracket one".

    The browser does the speaking, but the text preparation happens here so the
    behaviour is identical everywhere and is testable in Python.
    """
    # Normalise first: a full-width marker left in place would be spoken aloud as
    # a stray bracket, and would also survive the ASCII-only substitution below.
    normalized = normalize_citation_markers(text)
    without_code = strip_code(normalized)
    cleaned = CITATION_RE.sub("", without_code)
    cleaned = WEB_CITATION_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)   # " ." -> "."
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    return cleaned.strip()


def markdown_to_plain(text: str) -> str:
    """Flatten Markdown to speech-friendly plain text."""
    out = _CODE_FENCE_RE.sub(" (code block omitted) ", text)
    out = re.sub(r"^#{1,6}\s*", "", out, flags=re.MULTILINE)
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", out)
    out = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", out)
    out = re.sub(r"__(.+?)__", r"\1", out)
    out = re.sub(r"`([^`\n]+)`", r"\1", out)
    out = re.sub(r"^\s*[-*+]\s+", "", out, flags=re.MULTILINE)
    out = re.sub(r"^\s*\d+\.\s+", "", out, flags=re.MULTILINE)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", out)
    out = re.sub(r"^>\s?", "", out, flags=re.MULTILINE)
    out = re.sub(r"\|", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def speech_text(text: str) -> str:
    """Full pipeline used by Read Aloud."""
    return markdown_to_plain(strip_citations_for_speech(text))


def citations_by_number(report: CitationReport) -> dict[int, Citation]:
    return {c.number: c for c in report.citations}


def cited_document_ids(report: CitationReport) -> Iterable[int]:
    for citation in report.citations:
        if citation.document_id is not None:
            yield int(citation.document_id)
