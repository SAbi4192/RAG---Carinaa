"""Markdown and plain-text parser."""

from __future__ import annotations

import re
from pathlib import Path

from app.ingestion.parsers import register
from app.ingestion.types import NormalizedDocument, clean_text, looks_like_heading

_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)(.*)$")
_BULLET = re.compile(r"^\s*([-*+•]|\d+[.)])\s+\S")
_BLOCKQUOTE = re.compile(r"^\s*>\s?")
_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_MD_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")


@register(".md", ".markdown", label="Markdown")
def parse_markdown(path: Path, document_id: int, document_name: str) -> NormalizedDocument:
    doc = NormalizedDocument(
        document_id=document_id, document_name=document_name, file_type="markdown"
    )
    text = _decode(path.read_bytes())
    _walk_lines(doc, text, markdown=True)
    return doc


@register(".txt", ".text", ".log", label="Plain text")
def parse_text(path: Path, document_id: int, document_name: str) -> NormalizedDocument:
    doc = NormalizedDocument(
        document_id=document_id, document_name=document_name, file_type="txt"
    )
    text = _decode(path.read_bytes())
    _walk_lines(doc, text, markdown=False)
    return doc


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _walk_lines(doc: NormalizedDocument, text: str, *, markdown: bool) -> None:
    """Single pass over lines, tracking fenced code blocks and the current section.

    Code is kept as its own block type rather than being merged into prose. That
    matters twice over: code embeds differently from prose, and when the user
    expands a citation we want to show it as code, not as a run-on paragraph.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    section = ""
    buffer: list[str] = []
    in_fence = False
    fence_lines: list[str] = []
    fence_lang = ""
    pending_md_table: list[str] = []

    def flush_paragraph() -> None:
        nonlocal buffer
        if not buffer:
            return
        chunk = clean_text("\n".join(buffer))
        buffer = []
        if not chunk:
            return
        if looks_like_heading(chunk) and len(chunk) <= 120:
            doc.add_block(chunk, block_type="heading", section=chunk)
        else:
            doc.add_block(chunk, block_type="paragraph", section=section)

    def flush_table() -> None:
        nonlocal pending_md_table
        if not pending_md_table:
            return
        rows = [r for r in pending_md_table if not _MD_TABLE_SEP.match(r)]
        pending_md_table = []
        cleaned = []
        for row in rows:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            cells = [c for c in cells if c]
            if cells:
                cleaned.append(" | ".join(cells))
        if cleaned:
            header = cleaned[0]
            body = cleaned[1:]
            rendered = f"Table: {header}\n" + "\n".join(body) if body else header
            doc.add_block(rendered, block_type="table", section=section)

    for raw_line in lines:
        line = raw_line.rstrip()

        # ---- fenced code ------------------------------------------------
        fence_match = _FENCE.match(line)
        if fence_match:
            if in_fence:
                flush_paragraph()
                doc.add_block(
                    "\n".join(fence_lines),
                    block_type="code",
                    section=section,
                    language=fence_lang,
                )
                fence_lines, fence_lang, in_fence = [], "", False
            else:
                flush_paragraph()
                flush_table()
                in_fence = True
                fence_lang = fence_match.group(2).strip()
            continue

        if in_fence:
            fence_lines.append(raw_line)
            continue

        # ---- markdown tables -------------------------------------------
        if markdown and _MD_TABLE_ROW.match(line):
            flush_paragraph()
            pending_md_table.append(line)
            continue
        if pending_md_table:
            flush_table()

        # ---- blank line --------------------------------------------------
        if not line.strip():
            flush_paragraph()
            continue

        # ---- ATX heading -------------------------------------------------
        if markdown:
            heading_match = _ATX_HEADING.match(line)
            if heading_match:
                flush_paragraph()
                heading_text = clean_text(heading_match.group(2))
                section = heading_text[:150]
                doc.add_block(
                    heading_text,
                    block_type="heading",
                    section=section,
                    level=len(heading_match.group(1)),
                )
                continue

        # ---- blockquote / bullet ----------------------------------------
        if _BLOCKQUOTE.match(line):
            flush_paragraph()
            doc.add_block(
                _BLOCKQUOTE.sub("", line), block_type="caption", section=section
            )
            continue

        if _BULLET.match(line):
            flush_paragraph()
            doc.add_block(
                _BULLET.sub("", line, count=1), block_type="bullet", section=section
            )
            continue

        buffer.append(line)

    # ---- end of file ------------------------------------------------------
    if in_fence and fence_lines:
        doc.add_block("\n".join(fence_lines), block_type="code", section=section, language=fence_lang)
    flush_paragraph()
    flush_table()
