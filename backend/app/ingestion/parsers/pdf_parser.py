"""PDF parser - one block per page, plus heading-aware section tracking."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from app.core.logging import get_logger
from app.ingestion.parsers import register
from app.ingestion.types import NormalizedDocument, clean_text, looks_like_heading

logger = get_logger(__name__)

# A page yielding fewer characters than this is almost certainly scanned or
# image-only. We flag it instead of silently pretending we read it.
_MIN_CHARS_PER_PAGE = 12


@register(".pdf", label="PDF")
def parse_pdf(path: Path, document_id: int, document_name: str) -> NormalizedDocument:
    """Extract text page by page.

    WHY page-by-page: `page_number` is the single most useful citation field for
    a PDF. If we flattened the whole document into one blob we could never tell
    the user "this came from page 32". Page granularity is preserved here and
    survives all the way into the citation card.
    """
    doc = NormalizedDocument(
        document_id=document_id,
        document_name=document_name,
        file_type="pdf",
    )

    reader = PdfReader(str(path))

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # pragma: no cover
            doc.warnings.append("This PDF is password-protected and could not be opened.")
            logger.warning("Encrypted PDF %s: %s", document_name, exc)
            return doc

    meta = reader.metadata or {}
    doc.doc_metadata = {
        "title": str(meta.get("/Title", "") or ""),
        "author": str(meta.get("/Author", "") or ""),
        "subject": str(meta.get("/Subject", "") or ""),
        "producer": str(meta.get("/Producer", "") or ""),
        "creator": str(meta.get("/Creator", "") or ""),
    }

    page_count = len(reader.pages)
    doc.page_count = page_count

    empty_pages: list[int] = []
    current_section = ""

    for page_index, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception as exc:  # one bad page must not kill the document
            doc.warnings.append(f"Page {page_index} could not be parsed.")
            logger.debug("PDF page %s failed: %s", page_index, exc)
            continue

        text = clean_text(raw)
        if len(text) < _MIN_CHARS_PER_PAGE:
            empty_pages.append(page_index)
            continue

        # Split the page into paragraphs. Most PDFs lose paragraph structure on
        # extraction, so we split on blank lines first and fall back to lines.
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if len(paragraphs) <= 1:
            paragraphs = [ln.strip() for ln in text.split("\n") if ln.strip()]

        for para in paragraphs:
            if looks_like_heading(para):
                current_section = para[:150]
                block_type = "heading"
            else:
                block_type = "paragraph"

            doc.add_block(
                para,
                block_type=block_type,
                page_number=page_index,
                page_end=page_index,
                section=current_section,
            )

    if empty_pages:
        shown = empty_pages[:10]
        suffix = f" (+{len(empty_pages) - 10} more)" if len(empty_pages) > 10 else ""
        doc.warnings.append(
            f"{len(empty_pages)} of {page_count} pages contained no extractable text "
            f"(likely scanned images): pages {', '.join(map(str, shown))}{suffix}. "
            f"OCR is not performed."
        )

    return doc
