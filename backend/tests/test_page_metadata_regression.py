"""Regression tests for the page-metadata bug and the page-aware retrieval path.

THE BUG THIS LOCKS OUT
----------------------
"What is on Page Number 22? (in ADT_Notes.pdf)" wrongly reported that the
documents had no page information, even though all 176 PDF chunks carried
`page_number`/`page_end` metadata.

ROOT CAUSE (proven against the real 22-chunk + 176-chunk workspace): the range
helper `routes_chat._page_range` used SQLAlchemy's default JSON index operator,
which renders `JSON_QUOTE(JSON_EXTRACT(doc_metadata, '$.page_end'))`.
`JSON_QUOTE` wraps every value in quotes - numbers become strings - and for a
row with NO `page_end` (every TXT/CSV/MD chunk, and the PDF's own trailing rows)
it produces the TEXT `'null'`, not SQL NULL. `MAX()` over that mix collapses to
the string `'null'`, so the tuple came back `(1, None)` and the code concluded
"no page metadata". A workspace of only small PDFs (page_number == page_end)
never triggered it; a real mixed workspace always did.

THE FIX: cast the JSON path with `.as_integer()` / `.as_string()`, which emits a
plain JSON_EXTRACT typed to SQL and ignores NULLs. These tests assert the cast
survives the exact shape that used to break it.

Everything here runs against the throwaway test database (conftest redirects
`DATABASE_URL`), seeding synthetic chunks - no 2 GB model, no real files.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import Chunk, Document
from app.api.routes_chat import (
    _page_range,
    _paginated_scope_filenames,
    _scope_filenames,
)
from app.rag.understanding import detect_page_range, detect_page_reference


# ---------------------------------------------------------------------------
# Seeding: build the exact metadata shape that broke the old query
# ---------------------------------------------------------------------------
def _seed_paged_and_unpaged(db, workspace_id: int, user_id: int):
    """One PDF document (chunks with page metadata) + one TXT (chunks without).

    The MIX is the point. The regression only appears when paginated and
    non-paginated chunks share a workspace, because the non-paginated rows are
    what turn `MAX(page_end)` into the quoted string `'null'`.
    """
    pdf = Document(
        workspace_id=workspace_id,
        user_id=user_id,
        original_filename="ADT_Notes.pdf",
        stored_filename="adt.pdf",
        file_type="pdf",
        status="ready",
        page_count=65,
    )
    txt = Document(
        workspace_id=workspace_id,
        user_id=user_id,
        original_filename="notes.txt",
        stored_filename="notes.txt",
        file_type="txt",
        status="ready",
    )
    db.add_all([pdf, txt])
    db.commit()

    rows = []
    # PDF chunks: several pages, with page_number and page_end.
    for index in range(6):
        rows.append(
            Chunk(
                document_id=pdf.id,
                workspace_id=workspace_id,
                chunk_index=index,
                content=f"PDF passage {index} covering page {index + 20}.",
                doc_metadata={"page_number": index + 20, "page_end": index + 21},
            )
        )
    # TXT chunks: NO page metadata at all - the shape that produced `'null'`.
    for index in range(3):
        rows.append(
            Chunk(
                document_id=txt.id,
                workspace_id=workspace_id,
                chunk_index=index,
                content=f"Plain text passage {index}.",
                doc_metadata={"section": f"Part {index}"},
            )
        )
    db.add_all(rows)
    db.commit()
    return pdf.id, txt.id


# ---------------------------------------------------------------------------
# The regression itself: _page_range must see the real 20..21 span, not None
# ---------------------------------------------------------------------------
def test_page_range_survives_mixed_paginated_workspace(make_user, make_workspace, db):
    headers, user_id = make_user()
    workspace_id = make_workspace(headers)
    _seed_paged_and_unpaged(db, workspace_id, user_id)

    span = _page_range(db, None, workspace_id)
    # Before the fix this returned (1, None) or None because MAX(page_end)
    # collapsed to the string 'null'. The cast returns the honest numeric span.
    assert span is not None, "range must not be lost when unpaged chunks coexist"
    low, high = span
    assert low == 20 and high == 26, f"expected the real PDF span (20,26), got {span}"


def test_page_range_ignores_txt_chunks_without_pages(make_user, make_workspace, db):
    """TXT rows must not contribute a page bound at all - not even 0 or 'null'."""
    headers, user_id = make_user()
    workspace_id = make_workspace(headers)
    _pdf_id, txt_id = _seed_paged_and_unpaged(db, workspace_id, user_id)

    # Scoped to the text document only: it has no page metadata anywhere, so the
    # honest answer is None ("not paginated"), not 0, not 'null'.
    span = _page_range(db, [txt_id], workspace_id)
    assert span is None, "a non-paginated scope has no page range and must say so"


def test_page_range_with_explicit_scope(make_user, make_workspace, db):
    """Scoping to the PDF document alone yields exactly its page span."""
    headers, user_id = make_user()
    workspace_id = make_workspace(headers)
    pdf_id, _txt_id = _seed_paged_and_unpaged(db, workspace_id, user_id)

    assert _page_range(db, [pdf_id], workspace_id) == (20, 26)


# ---------------------------------------------------------------------------
# Page ranges (Phase 12)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "question,expected",
    [
        ("Summarize pages 20-22.", (20, 22)),
        ("What is on pages 21 to 25?", (21, 25)),
        ("Explain pages 10 through 15", (10, 15)),
        ("tell me about pages 3 - 6", (3, 6)),
    ],
)
def test_page_range_is_detected(question: str, expected: tuple[int, int]) -> None:
    found = detect_page_range(question)
    assert found is not None
    assert (found[0], found[1]) == expected


@pytest.mark.parametrize(
    "question",
    [
        "What is on page 22?",      # single page, not a range
        "Summarize this document.", # no pages at all
        "pages 5-3 is reversed",    # reversed range -> rejected, not swapped
    ],
)
def test_non_ranges_are_not_treated_as_ranges(question: str) -> None:
    assert detect_page_range(question) is None


def test_single_page_reference_is_not_a_range() -> None:
    """detect_page_reference owns a single page; a one-page 'range' defers to it."""
    assert detect_page_range("Summarize pages 22-22.") is None
    number = detect_page_reference("Summarize pages 22-22.")[0]
    assert number == 22


# ---------------------------------------------------------------------------
# Ambiguity only considers PAGINATED documents (Phase 11)
# ---------------------------------------------------------------------------
def test_page_query_is_ambiguous_only_across_pdfs(make_user, make_workspace, db):
    """Two PDFs -> ambiguous; a TXT in the workspace must NOT add false ambiguity."""
    headers, user_id = make_user()
    workspace_id = make_workspace(headers)

    a = Document(
        workspace_id=workspace_id, user_id=user_id, original_filename="A.pdf",
        stored_filename="a.pdf", file_type="pdf", status="ready",
    )
    b = Document(
        workspace_id=workspace_id, user_id=user_id, original_filename="B.pdf",
        stored_filename="b.pdf", file_type="pdf", status="ready",
    )
    t = Document(
        workspace_id=workspace_id, user_id=user_id, original_filename="data.txt",
        stored_filename="data.txt", file_type="txt", status="ready",
    )
    db.add_all([a, b, t])
    db.commit()

    # Full scope names every file (what the old, buggy code passed in).
    all_names = _scope_filenames(db, None, workspace_id)
    assert set(all_names) == {"A.pdf", "B.pdf", "data.txt"}

    # Paginated-only set: the TXT cannot answer a page question, so it is excluded.
    paged = _paginated_scope_filenames(db, None, workspace_id)
    assert set(paged) == {"A.pdf", "B.pdf"}


def test_txt_only_workspace_has_no_page_ambiguity(make_user, make_workspace, db):
    """A page question in a workspace with only TXT files must not offer candidates."""
    headers, user_id = make_user()
    workspace_id = make_workspace(headers)
    for name in ("one.txt", "two.csv", "three.md"):
        db.add(
            Document(
                workspace_id=workspace_id, user_id=user_id,
                original_filename=name, stored_filename=name,
                file_type=name.rsplit(".", 1)[1], status="ready",
            )
        )
    db.commit()
    assert _paginated_scope_filenames(db, None, workspace_id) == []
