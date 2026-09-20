"""
XLSX parser.

WHY SPREADSHEETS ARE DIFFERENT
------------------------------
Spec section 51: "Do not automatically treat spreadsheets as plain prose."

A spreadsheet row is not a paragraph. If you flatten a sheet into running text you
lose the column names, and a chunk that says `Q3 | 12 | 9` is meaningless both to
the embedding model and to a human reading the citation.

So we do the structural thing instead:

    Workbook -> Sheet -> header row detected -> each row rendered as
    "Header: value | Header: value"

That single transformation makes rows:
  * self-describing  -> retrieves on the meaning of the column names
  * citable          -> we can say "Sheet: Revenue, rows 42-43"
  * readable         -> the citation card shows something a human understands

We deliberately do NOT execute formulas or run any code over the workbook
(spec section 65). Values are read with `data_only=True`, so cached results are
used where Excel saved them.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from app.ingestion.parsers import register
from app.ingestion.types import NormalizedDocument, clean_text

# Guard rails so one enormous sheet cannot exhaust memory during ingestion.
_MAX_ROWS_PER_SHEET = 5000
_MAX_COLUMNS = 60
_MAX_CELL_CHARS = 500


def _cell_to_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if len(text) > _MAX_CELL_CHARS:
        text = text[:_MAX_CELL_CHARS] + "..."
    return text


def _looks_like_header(row: tuple[object, ...], next_row: tuple[object, ...] | None) -> bool:
    """Decide whether the first populated row holds column names.

    Heuristic, and deliberately conservative:
      * mostly non-empty, mostly text  -> header
      * if the row below is mostly numbers while this row is mostly text -> header
    """
    cells = [_cell_to_text(c) for c in row]
    filled = [c for c in cells if c]
    if len(filled) < 2:
        return False

    text_like = sum(1 for c in filled if not _is_number(c))
    if text_like / len(filled) < 0.6:
        return False

    if next_row:
        below = [_cell_to_text(c) for c in next_row]
        below_filled = [c for c in below if c]
        if below_filled:
            below_numeric = sum(1 for c in below_filled if _is_number(c))
            if below_numeric / len(below_filled) > 0.5:
                return True

    # Single-letter/very short column labels are usually real headers.
    return all(len(c) <= 60 for c in filled)


def _is_number(text: str) -> bool:
    try:
        float(text.replace(",", "").replace("%", "").replace("₹", "").replace("$", ""))
        return True
    except (ValueError, AttributeError):
        return False


@register(".xlsx", ".xlsm", label="Excel workbook")
def parse_xlsx(path: Path, document_id: int, document_name: str) -> NormalizedDocument:
    if path.suffix.lower() == ".xls":
        raise ValueError("Legacy .xls files are not supported. Please re-save as .xlsx.")

    doc = NormalizedDocument(
        document_id=document_id, document_name=document_name, file_type="xlsx"
    )

    # read_only keeps memory flat on big workbooks; data_only reads cached values.
    workbook = load_workbook(filename=str(path), read_only=True, data_only=True)

    doc.doc_metadata = {"sheet_names": list(workbook.sheetnames)}
    doc.sheet_names = list(workbook.sheetnames)

    for sheet in workbook.worksheets:
        rows_iter = sheet.iter_rows(values_only=True)

        collected: list[tuple[int, tuple[object, ...]]] = []
        for row_number, row in enumerate(rows_iter, start=1):
            if row is None:
                continue
            trimmed = tuple(row[:_MAX_COLUMNS])
            if not any(_cell_to_text(c) for c in trimmed):
                continue
            collected.append((row_number, trimmed))
            if len(collected) >= _MAX_ROWS_PER_SHEET:
                doc.warnings.append(
                    f"Sheet '{sheet.title}' was truncated at {_MAX_ROWS_PER_SHEET} non-empty rows."
                )
                break

        if not collected:
            doc.warnings.append(f"Sheet '{sheet.title}' is empty and was skipped.")
            continue

        first_row_number, first_row = collected[0]
        second_row = collected[1][1] if len(collected) > 1 else None
        has_header = _looks_like_header(first_row, second_row)

        headers: list[str] = []
        if has_header:
            headers = [
                _cell_to_text(c) or f"Column {i + 1}" for i, c in enumerate(first_row)
            ]

        # A short orientation block: what is this sheet, and what are its columns?
        # This is what answers questions like "what does the Revenue sheet track?"
        if headers:
            doc.add_block(
                f"Sheet '{sheet.title}' has columns: {', '.join(headers)}.",
                block_type="heading",
                sheet_name=sheet.title,
                section=sheet.title,
                row_start=first_row_number,
                row_end=first_row_number,
                element="sheet_summary",
            )

        data_rows = collected[1:] if has_header else collected

        for row_number, row in data_rows:
            values = [_cell_to_text(c) for c in row]
            pairs: list[str] = []
            for col_index, value in enumerate(values):
                if not value:
                    continue
                if headers and col_index < len(headers):
                    label = headers[col_index]
                else:
                    label = f"Column {col_index + 1}"
                pairs.append(f"{label}: {value}")

            if not pairs:
                continue

            doc.add_block(
                f"[{sheet.title}] " + " | ".join(pairs),
                block_type="table_row",
                sheet_name=sheet.title,
                section=sheet.title,
                row_start=row_number,
                row_end=row_number,
                has_header=has_header,
            )

    workbook.close()

    if not doc.sheet_names:
        doc.warnings.append("This workbook contains no sheets.")

    return doc
