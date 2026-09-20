"""Near-duplicate suppression in retrieval.

The bug: the same document uploaded twice, or as both a PDF and an exported
Markdown/Word file, produces chunks that are the same evidence but not the same
string. Converters change hyphenation, whitespace, soft line breaks and header
text, so exact-string dedupe misses them entirely.

That matters more than it sounds. The copies then compete for the same `top_k`
slots, so with a document present three times, five excerpt slots can be filled by
roughly two distinct passages. The model sees the same evidence repeatedly while
genuinely different evidence is pushed out of the context - and the source list
shows the copies as separate sources, implying broader support than exists.

These tests pin both directions: duplicates must collapse, and distinct evidence
must survive. Getting only the first right would silently destroy recall.
"""

from __future__ import annotations

import pytest

from app.rag.retriever import Retriever, _content_word_set


def chunk(content: str, score: float, document_id: int, name: str):
    from app.rag.vectorstore import RetrievedChunk

    return RetrievedChunk(
        vector_id=f"{document_id}-{abs(hash(content)) % 10000}",
        workspace_id=21,
        document_id=document_id,
        chunk_index=0,
        content=content,
        metadata={},
        score=score,
        rank=0,
        chunk_id=None,
        document_name=name,
        file_type="pdf" if name.endswith(".pdf") else "md",
    )


PDF_TEXT = (
    "4.7  Systematic Concept Generation\n"
    "Systematic  concept  generation  outlines  steps  for  defining  problems,\n"
    "researching  insights,  brainstorming  ideas,  and  presenting  concepts.  A\n"
    "sustainable  packaging  case  study  is  used  to  illustrate  the  process."
)

MD_TEXT = (
    "## Systematic Concept Generation\n"
    "Systematic concept generation outlines steps for defining problems, "
    "researching insights, brainstorming ideas, and presenting concepts. "
    "A sustainable packaging case study is used to illustrate the process."
)

DISTINCT_TEXT = (
    "A value proposition canvas maps customer pains, gains and jobs to be done "
    "against the product features that address them, so teams can check fit "
    "before committing to a build."
)


def dedupe(candidates):
    return Retriever._dedupe(Retriever(), candidates)


def test_identical_text_collapses_to_one() -> None:
    kept, removed = dedupe(
        [chunk(PDF_TEXT, 0.30, 1, "a.pdf"), chunk(PDF_TEXT, 0.28, 1, "a.pdf")]
    )
    assert len(kept) == 1
    assert removed == 1


def test_pdf_and_markdown_export_of_the_same_paragraph_collapse() -> None:
    """The real-world case: same content, different converter output."""
    kept, removed = dedupe(
        [chunk(PDF_TEXT, 0.275, 7, "ADT_Notes.pdf"), chunk(MD_TEXT, 0.270, 15, "ADT_Notes.md")]
    )
    assert len(kept) == 1
    assert removed == 1
    # The copy that matched the question better is the one that survives.
    assert kept[0].document_name == "ADT_Notes.pdf"


def test_distinct_evidence_survives() -> None:
    kept, removed = dedupe(
        [chunk(PDF_TEXT, 0.30, 1, "a.pdf"), chunk(DISTINCT_TEXT, 0.28, 1, "a.pdf")]
    )
    assert len(kept) == 2
    assert removed == 0


def test_three_copies_collapse_to_one_keeping_the_best() -> None:
    kept, removed = dedupe(
        [
            chunk(MD_TEXT, 0.20, 15, "ADT_Notes.md"),
            chunk(PDF_TEXT, 0.29, 30, "ADT_Notes.pdf"),
            chunk(PDF_TEXT, 0.21, 7, "ADT_Notes.pdf"),
        ]
    )
    assert len(kept) == 1
    assert removed == 2
    assert kept[0].score == pytest.approx(0.29)


def test_duplicates_do_not_consume_top_k_slots() -> None:
    """The consequence the fix exists for.

    Five slots must hold five DISTINCT passages even when every passage is present
    twice. Before the fix the copies took the slots, so the model saw roughly half
    as much real evidence as the settings implied.

    The two copies of each passage differ the way a real PDF extraction and its
    Markdown export differ: a section number and doubled spacing versus a heading
    marker and normal spacing. No wording changes - which is why this is the
    realistic case rather than an artificial one.
    """
    passages = [
        "Systematic concept generation outlines steps for defining problems and researching insights",
        "Document and communicate stresses recording technical specifications and economic viability",
        "Evaluation of technology alternatives checks whether chosen options meet project requirements",
        "The innovation rubric scores feasibility across technical resource and operational dimensions",
        "Creating user personas lists demographics goals challenges and behavioural preferences here",
    ]

    candidates = []
    for index, passage in enumerate(passages):
        score = 0.30 - index * 0.01
        pdf_variant = f"4.{index}  " + passage.replace(" ", "  ") + " ."
        md_variant = f"## {passage}."
        candidates.append(chunk(pdf_variant, score, 7, "a.pdf"))
        candidates.append(chunk(md_variant, score - 0.001, 15, "a.md"))

    kept, removed = dedupe(candidates)
    assert removed == 5, "one copy of each passage should have been collapsed"
    assert len(kept) == 5

    top5 = kept[:5]
    assert len(top5) == 5
    # Each survivor is a different passage, and always the better-scoring copy.
    assert {c.document_name for c in top5} == {"a.pdf"}
    assert len({" ".join(c.content.lower().split()) for c in top5}) == 5


def test_empty_and_single_candidate_are_handled() -> None:
    assert dedupe([]) == ([], 0)
    single = chunk(PDF_TEXT, 0.5, 1, "a.pdf")
    kept, removed = dedupe([single])
    assert kept == [single]
    assert removed == 0


def test_word_set_ignores_punctuation_and_case() -> None:
    assert _content_word_set("User-Persona: Goals!") == _content_word_set("user persona goals")


def test_threshold_disabled_keeps_exact_dedupe_only() -> None:
    """Setting the threshold to 0 must disable pass 2 but keep pass 1, so there is
    a supported way to opt out if the heuristic ever misfires."""
    from app.core.config import settings

    original = settings.retrieval_near_duplicate_threshold
    settings.retrieval_near_duplicate_threshold = 0.0
    try:
        kept, removed = dedupe(
            [
                chunk(PDF_TEXT, 0.275, 7, "a.pdf"),
                chunk(MD_TEXT, 0.270, 15, "a.md"),
            ]
        )
        # Near-duplicate survives when pass 2 is off.
        assert len(kept) == 2
        assert removed == 0

        # Exact duplicates are still collapsed.
        kept, removed = dedupe(
            [chunk(PDF_TEXT, 0.3, 1, "a.pdf"), chunk(PDF_TEXT, 0.2, 1, "a.pdf")]
        )
        assert len(kept) == 1
        assert removed == 1
    finally:
        settings.retrieval_near_duplicate_threshold = original
