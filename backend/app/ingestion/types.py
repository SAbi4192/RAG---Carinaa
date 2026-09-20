"""
The common internal representation every parser must produce.

WHY THIS FILE EXISTS
--------------------
We support eight input formats (PDF, DOCX, PPTX, XLSX, CSV, Markdown, TXT,
JSON). Without a shared shape, every downstream component would need eight
branches. Instead each parser - and only the parser - knows about its format.
It converts its output into `NormalizedBlock` objects and hands them over.

From that point on, nothing in the pipeline cares whether the text came from
page 32 of a PDF or row 14 of an Excel sheet. It just sees text plus provenance.

    PDF  ─┐
    DOCX ─┤
    PPTX ─┤                        ┌──────────┐
    XLSX ─┼─► format-specific ───► │ Normalized│ ──► normalise ──► chunk ──► embed
    CSV  ─┤      parser            │ Document │
    MD   ─┤                        └──────────┘
    TXT  ─┤
    JSON ─┘

PROVENANCE KEYS (spec section 6)
--------------------------------
    document_id, document_name, file_type
    page_number      - PDF / DOCX page (1-based)
    slide_number     - PPTX slide (1-based)
    sheet_name       - XLSX / CSV sheet
    section          - nearest preceding heading
    row_start/row_end- spreadsheet row range (1-based, inclusive)
    json_path        - JSON dotted path, e.g. "results[0].summary"
    block_index      - position of the block within the document
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

BlockType = Literal[
    "paragraph",
    "heading",
    "bullet",
    "table_row",
    "table",
    "code",
    "json_leaf",
    "caption",
    "page_marker",
]

# Keys we promise to preserve all the way through to the citation card.
PROVENANCE_KEYS: tuple[str, ...] = (
    "page_number",
    "page_end",
    "slide_number",
    "sheet_name",
    "section",
    "row_start",
    "row_end",
    "column_range",
    "json_path",
    "block_index",
)


@dataclass(slots=True)
class NormalizedBlock:
    """One atomic piece of extracted content, with provenance attached."""

    text: str
    block_type: BlockType = "paragraph"
    order: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.metadata.setdefault("block_index", self.order)
        self.metadata.setdefault("block_type", self.block_type)

    @property
    def length(self) -> int:
        return len(self.text)


@dataclass(slots=True)
class NormalizedDocument:
    """Everything a parser extracted, in reading order."""

    document_id: int
    document_name: str
    file_type: str
    blocks: list[NormalizedBlock] = field(default_factory=list)

    # Format-level facts used by the UI and by the analytics page.
    page_count: int = 0
    sheet_names: list[str] = field(default_factory=list)
    slide_count: int = 0

    # Non-fatal problems. Shown in the UI; they do not fail the ingestion.
    warnings: list[str] = field(default_factory=list)

    # Parser-reported metadata (author, title, created date, ...).
    doc_metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    @property
    def text(self) -> str:
        """All block text joined in order. Used for stats, not for retrieval."""
        return "\n\n".join(b.text for b in self.blocks if b.text)

    @property
    def char_count(self) -> int:
        return sum(b.length for b in self.blocks)

    @property
    def is_empty(self) -> bool:
        return not any(b.text.strip() for b in self.blocks)

    def base_metadata(self) -> dict[str, Any]:
        """Provenance shared by every block in this document."""
        return {
            "document_id": self.document_id,
            "document_name": self.document_name,
            "file_type": self.file_type,
        }

    def add_block(
        self,
        text: str,
        block_type: BlockType = "paragraph",
        **metadata: Any,
    ) -> NormalizedBlock | None:
        """Append a block. Returns None (and records nothing) for blank text."""
        cleaned = text.strip()
        if not cleaned:
            return None
        block = NormalizedBlock(
            text=cleaned,
            block_type=block_type,
            order=len(self.blocks),
            metadata={**self.base_metadata(), **metadata},
        )
        self.blocks.append(block)
        return block

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        for block in self.blocks:
            by_type[block.block_type] = by_type.get(block.block_type, 0) + 1
        return {
            "blocks": len(self.blocks),
            "characters": self.char_count,
            "pages": self.page_count,
            "slides": self.slide_count,
            "sheets": len(self.sheet_names),
            "block_types": by_type,
        }


# ---------------------------------------------------------------------------
# Text normalisation helpers (shared by every parser)
# ---------------------------------------------------------------------------
_WHITESPACE_RUN = re.compile(r"[ \t\u00a0\u2007\u202f]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_HYPHEN_LINEBREAK = re.compile(r"(\w)-\n(\w)")
_SOFT_LINEBREAK = re.compile(r"(?<=[a-z,;])\n(?=[a-z])")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text: str) -> str:
    """Normalise whitespace without destroying paragraph structure.

    Deliberately conservative: we collapse runs of spaces and excess blank
    lines, repair hyphenated line breaks, and strip control characters. We do
    NOT lowercase, stem, or remove punctuation - that would change what the
    citation shows the user, and the embedding model already handles casing.
    """
    if not text:
        return ""
    text = _CONTROL_CHARS.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HYPHEN_LINEBREAK.sub(r"\1\2", text)   # "utiliza-\ntion" -> "utilization"
    text = _SOFT_LINEBREAK.sub(" ", text)         # join wrapped prose lines
    text = _WHITESPACE_RUN.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def looks_like_heading(text: str) -> bool:
    """Heuristic heading detector used for section tracking and structure-aware splitting."""
    stripped = text.strip()
    if not stripped or len(stripped) > 120:
        return False
    if stripped.endswith((".", ",", ";", ":")):
        # "1. Introduction" is a heading; "This is a sentence." is not.
        return bool(re.match(r"^(\d+(\.\d+)*|[IVXLC]+)[.)]\s+\S", stripped))
    if stripped.isupper() and len(stripped) > 3:
        return True
    if re.match(r"^#{1,6}\s+\S", stripped):           # Markdown ATX
        return True
    if re.match(r"^(\d+(\.\d+)*)[.)]?\s+[A-Z]\S", stripped):
        return True
    words = stripped.split()
    return len(words) <= 12 and stripped[0].isupper() and not stripped.endswith(("!", "?"))


def detect_language_hint(text: str) -> str:
    """Very small script detector. Used only to pick a nicer empty-state message."""
    if re.search(r"[\u0B80-\u0BFF]", text):
        return "ta"
    if re.search(r"[\u0D00-\u0D7F]", text):
        return "ml"
    if re.search(r"[\u0C00-\u0C7F]", text):
        return "te"
    if re.search(r"[\u0900-\u097F]", text):
        return "hi"
    if re.search(r"[\u3040-\u30FF]", text):
        return "ja"
    return "en"
