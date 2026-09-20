"""
Normalisation - the pass between "parsed" and "chunked".

WHY THIS STAGE EXISTS
---------------------
Parsers are optimised for *their format*, not for quality. A PDF extractor will
happily hand you a repeated running header on every page; a PPTX will give you the
same footer 40 times; a DOCX will include an empty paragraph for every line break
the author ever pressed. All of that becomes noise in the vector index.

This stage does the cleanup, in one place, for every format:

  1. re-clean whitespace (parsers do this too - it is idempotent and cheap)
  2. drop empty and whitespace-only blocks
  3. drop repeated boilerplate (the same short line appearing many times)
  4. drop exact duplicate blocks, keeping the first occurrence
  5. renumber blocks so `block_index` stays contiguous
  6. report what it removed, so the UI can show real numbers

Step 6 matters for the demo: after uploading a lecture PDF you can show the
instructor "the parser produced 812 blocks; normalisation removed 63 repeated
page headers, leaving 749". That is a real, checkable number.

WHAT WE DO NOT DO
-----------------
We do not lowercase, stem, remove stop-words, or strip punctuation. Those are
*search-time* techniques, and applying them here would change the text the user
sees in a citation. The embedding model handles case and morphology itself.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.ingestion.types import NormalizedBlock, NormalizedDocument, clean_text

# A short block repeated at least this many times is treated as boilerplate.
_BOILERPLATE_MIN_REPEATS = 4
_BOILERPLATE_MAX_CHARS = 90


@dataclass
class NormalizationReport:
    """Real counts from this run. Surfaced in the UI and in the trace."""

    blocks_in: int = 0
    blocks_out: int = 0
    removed_empty: int = 0
    removed_duplicate: int = 0
    removed_boilerplate: int = 0
    boilerplate_samples: list[str] = field(default_factory=list)
    cleaned_chars: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "blocks_in": self.blocks_in,
            "blocks_out": self.blocks_out,
            "removed_empty": self.removed_empty,
            "removed_duplicate": self.removed_duplicate,
            "removed_boilerplate": self.removed_boilerplate,
            "removed_total": self.blocks_in - self.blocks_out,
            "boilerplate_samples": self.boilerplate_samples[:5],
            "characters": self.cleaned_chars,
        }


def normalize_document(document: NormalizedDocument) -> tuple[NormalizedDocument, NormalizationReport]:
    """Clean a parsed document in place and return a report of what changed."""
    report = NormalizationReport(blocks_in=len(document.blocks))
    if not document.blocks:
        return document, report

    # ---- pass 1: clean + drop empties -------------------------------------
    cleaned: list[NormalizedBlock] = []
    for block in document.blocks:
        text = clean_text(block.text)
        if not text:
            report.removed_empty += 1
            continue
        block.text = text
        cleaned.append(block)

    # ---- pass 2: detect repeated boilerplate ------------------------------
    # Only consider SHORT blocks. A repeated paragraph could legitimately be
    # important (a definition restated per chapter), but a repeated 40-character
    # line is almost always a header, footer or slide template element.
    short_counts = Counter(
        b.text for b in cleaned if len(b.text) <= _BOILERPLATE_MAX_CHARS
    )
    boilerplate = {
        text
        for text, count in short_counts.items()
        if count >= _BOILERPLATE_MIN_REPEATS and not _is_structurally_useful(text)
    }
    if boilerplate:
        report.boilerplate_samples = sorted(boilerplate, key=len, reverse=True)[:5]

    # ---- pass 3: dedupe + drop boilerplate --------------------------------
    seen: set[str] = set()
    kept: list[NormalizedBlock] = []

    for block in cleaned:
        if block.text in boilerplate:
            report.removed_boilerplate += 1
            continue

        fingerprint = _fingerprint(block.text)
        if fingerprint in seen:
            report.removed_duplicate += 1
            continue
        seen.add(fingerprint)

        block.order = len(kept)
        block.metadata["block_index"] = len(kept)
        kept.append(block)

    document.blocks = kept
    report.blocks_out = len(kept)
    report.cleaned_chars = sum(b.length for b in kept)
    return document, report


def _is_structurally_useful(text: str) -> bool:
    """Protect short blocks that carry structure rather than noise.

    A repeated "Chapter 1" heading is boilerplate; a repeated "Revenue: 0" in a
    spreadsheet column is data. We keep anything that looks like a key/value pair
    or a heading, because dropping real data is much worse than keeping noise.
    """
    stripped = text.strip()
    if ":" in stripped or "|" in stripped:
        return True
    if stripped.startswith("#") or stripped.endswith(":"):
        return True
    if stripped.isupper() and len(stripped) > 3:
        return True
    return False


def _fingerprint(text: str) -> str:
    """Case- and whitespace-insensitive key for duplicate detection."""
    return " ".join(text.lower().split())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_document(document: NormalizedDocument) -> list[str]:
    """Return non-fatal warnings about a parsed document.

    These are shown in the Knowledge Base UI next to the document. A warning never
    blocks ingestion - a document that is 80% readable is still worth indexing, as
    long as we are honest about the other 20%.
    """
    warnings: list[str] = list(document.warnings)

    if document.char_count < 200:
        warnings.append(
            "This document contains very little text, so answers grounded in it will "
            "be limited."
        )

    total_blocks = len(document.blocks)
    if total_blocks:
        heading_ratio = sum(1 for b in document.blocks if b.block_type == "heading") / total_blocks
        if heading_ratio > 0.5:
            warnings.append(
                "More than half of this document's blocks look like headings. The text "
                "may not have extracted cleanly."
            )

    if document.page_count and not any(
        b.metadata.get("page_number") for b in document.blocks
    ):
        warnings.append("Page numbers could not be determined for this document.")

    return warnings


def summarize(document: NormalizedDocument, report: NormalizationReport) -> dict[str, Any]:
    """Combined stats block stored on the Document row and shown in the UI."""
    stats = document.stats()
    stats["normalization"] = report.as_dict()
    stats["warnings"] = validate_document(document)
    return stats
