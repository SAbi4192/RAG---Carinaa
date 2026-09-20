"""CSV parser - dialect-sniffing, then the same row rendering as XLSX."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from app.ingestion.parsers import register
from app.ingestion.types import NormalizedDocument, clean_text

_MAX_ROWS = 5000
_MAX_CELL_CHARS = 500


@register(".csv", ".tsv", label="CSV / TSV")
def parse_csv(path: Path, document_id: int, document_name: str) -> NormalizedDocument:
    """Read a delimited file as a one-sheet table.

    Encoding is the classic failure mode here. We try UTF-8, then UTF-8 with a
    BOM (very common from Excel on Windows), then cp1252, then latin-1 as a last
    resort. latin-1 never fails, so we always get *something* rather than a crash.
    """
    doc = NormalizedDocument(
        document_id=document_id, document_name=document_name, file_type="csv"
    )

    raw_bytes = path.read_bytes()
    text = _decode(raw_bytes)
    if not text.strip():
        return doc

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect)
    rows: list[tuple[int, list[str]]] = []
    for row_number, row in enumerate(reader, start=1):
        if not row or not any(cell.strip() for cell in row):
            continue
        rows.append((row_number, [c.strip()[:_MAX_CELL_CHARS] for c in row]))
        if len(rows) >= _MAX_ROWS:
            doc.warnings.append(f"File truncated at {_MAX_ROWS} non-empty rows.")
            break

    if not rows:
        return doc

    sheet_name = path.stem
    doc.sheet_names = [sheet_name]

    header_row_number, header_row = rows[0]
    headers = [c or f"Column {i + 1}" for i, c in enumerate(header_row)]

    doc.add_block(
        f"'{sheet_name}' has columns: {', '.join(headers)}.",
        block_type="heading",
        sheet_name=sheet_name,
        section=sheet_name,
        row_start=header_row_number,
        row_end=header_row_number,
        element="sheet_summary",
        delimiter=getattr(dialect, "delimiter", ","),
    )

    for row_number, row in rows[1:]:
        pairs = []
        for index, value in enumerate(row):
            if not value:
                continue
            label = headers[index] if index < len(headers) else f"Column {index + 1}"
            pairs.append(f"{label}: {value}")
        if pairs:
            doc.add_block(
                f"[{sheet_name}] " + " | ".join(pairs),
                block_type="table_row",
                sheet_name=sheet_name,
                section=sheet_name,
                row_start=row_number,
                row_end=row_number,
            )

    return doc


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# Keep the normaliser import referenced so linters do not drop it; CSV cells are
# already cleaned individually, but whole-file cleaning is used by the pipeline.
_ = clean_text
