"""PPTX parser - one slide at a time, with speaker notes included."""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation

from app.ingestion.parsers import register
from app.ingestion.types import NormalizedDocument, clean_text

# Shapes whose text is decoration rather than content.
_SKIP_SHAPE_TYPES = {"SLIDE_NUMBER", "DATE", "FOOTER"}


@register(".pptx", ".ppt", label="PowerPoint")
def parse_pptx(path: Path, document_id: int, document_name: str) -> NormalizedDocument:
    """Extract slides.

    Slides are inherently short and self-contained, which makes `slide_number`
    an excellent citation anchor. We keep each shape as its own block so that a
    title, a bullet list and a table do not get mashed into one blob - that keeps
    retrieval precise and lets the citation card show exactly which element the
    answer came from.

    NOTE: the legacy binary `.ppt` format is NOT supported by python-pptx. If a
    `.ppt` is uploaded we surface a clear error rather than an empty document.
    """
    if path.suffix.lower() == ".ppt":
        raise ValueError(
            "Legacy .ppt files are not supported. Please re-save as .pptx."
        )

    doc = NormalizedDocument(
        document_id=document_id, document_name=document_name, file_type="pptx"
    )

    presentation = Presentation(str(path))
    core = presentation.core_properties
    doc.doc_metadata = {
        "title": core.title or "",
        "author": core.author or "",
        "subject": core.subject or "",
        "created": str(core.created) if core.created else "",
    }

    slides = list(presentation.slides)
    doc.slide_count = len(slides)

    for slide_number, slide in enumerate(slides, start=1):
        slide_title = _slide_title(slide)
        section = slide_title or f"Slide {slide_number}"

        if slide_title:
            doc.add_block(
                slide_title,
                block_type="heading",
                slide_number=slide_number,
                section=section,
                element="title",
            )

        for shape_index, shape in enumerate(slide.shapes):
            if shape.shape_type is not None and str(shape.shape_type) in _SKIP_SHAPE_TYPES:
                continue

            # ---- tables ------------------------------------------------
            if getattr(shape, "has_table", False) and shape.has_table:
                table = shape.table
                rows = [[clean_text(c.text) for c in row.cells] for row in table.rows]
                if rows:
                    header, *body = rows
                    lines = []
                    for row in body:
                        pairs = [
                            f"{header[i] if i < len(header) and header[i] else f'Column {i+1}'}: {cell}"
                            for i, cell in enumerate(row)
                            if cell
                        ]
                        if pairs:
                            lines.append(" | ".join(pairs))
                    doc.add_block(
                        "\n".join(lines) or " | ".join(header),
                        block_type="table",
                        slide_number=slide_number,
                        section=section,
                        shape_index=shape_index,
                    )
                continue

            # ---- text frames -------------------------------------------
            if not getattr(shape, "has_text_frame", False) or not shape.has_text_frame:
                continue

            for paragraph in shape.text_frame.paragraphs:
                text = clean_text(paragraph.text)
                if not text:
                    continue
                # A bulleted line is level > 0 or explicitly bulleted.
                is_bullet = bool(getattr(paragraph, "level", 0)) or text.lstrip().startswith(
                    ("•", "-", "–", "*")
                )
                doc.add_block(
                    text,
                    block_type="bullet" if is_bullet else "paragraph",
                    slide_number=slide_number,
                    section=section,
                    shape_index=shape_index,
                )

        # ---- speaker notes ---------------------------------------------
        # Notes frequently hold the explanation that the slide only hints at, so
        # they are genuinely useful context - but we label them so a citation can
        # show the user "this came from the speaker notes", not the visible slide.
        if slide.has_notes_slide:
            notes_frame = slide.notes_slide.notes_text_frame
            notes = clean_text(notes_frame.text if notes_frame is not None else "")
            if notes:
                doc.add_block(
                    notes,
                    block_type="caption",
                    slide_number=slide_number,
                    section=section,
                    element="speaker_notes",
                )

    return doc


def _slide_title(slide) -> str:  # noqa: ANN001
    try:
        if slide.shapes.title is not None:
            return clean_text(slide.shapes.title.text)
    except (AttributeError, ValueError):
        pass
    return ""
