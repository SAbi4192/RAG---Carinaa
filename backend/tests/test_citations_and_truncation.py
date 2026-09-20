"""Regression tests for citation parsing and provider truncation.

Three bugs found by reading real API responses rather than by unit tests. All were
silent: nothing raised, nothing logged, and the output looked complete.

BUG 1 - grouped citation markers were invisible
------------------------------------------------
`CITATION_RE` used to be `\\[(\\d{1,3})\\]`, which matches `[1]` but not `[2, 4]`.
A real generated answer contained:

    "...allows cloud providers to pool resources ... and scale elastically [2, 4]."

Only citation 1 was resolved. Citations 2 and 4 - the evidence for that sentence -
never existed, so the reader could not check the claim. The same blind spot meant
`validate_translation` compared `[1]` against `[1]` and reported success even if a
translation had dropped the whole group.

BUG 2 - only Gemini reported truncation
---------------------------------------
Truncation detection lived in the Gemini provider alone, comparing
`finishReason == "MAX_TOKENS"`. Groq and llama.cpp report `finish_reason == "length"`
instead. So when Gemini rate-limited us and Groq produced a Tamil translation that
stopped mid-sentence, the response carried no warning at all and validation passed.

BUG 3 - the Groq fallback cites with full-width brackets
--------------------------------------------------------
Gemini writes `[1]`. Groq wrote `【1】` (U+3010/U+3011). Every regex here expected
ASCII, so a well-cited Groq answer was reported as citing nothing at all:

    citations: 0
    grounding: PARTIALLY_SUPPORTED - "the answer did not include any [citations]"

Because Gemini rate-limits often, the fallback is a common path - so this broke
citations on exactly the runs where the system was already degraded.
"""

from __future__ import annotations

import pytest

from app.features.validation import validate_translation
from app.llm.base import LLMResponse, is_truncated
from app.rag.citations import (
    CITATION_RE,
    count_citation_occurrences,
    extract_citation_numbers,
    normalize_citation_markers,
    resolve_citations,
    strip_citations_for_speech,
)


# ---------------------------------------------------------------------------
# Bug 1 - grouped markers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("scale elastically [2, 4].", [2, 4]),
        ("a [1]. b [2,4]. c [1,2,3].", [1, 2, 4, 3]),
        ("a single [12]", [12]),
        ("a three-digit [100]", [100]),
        ("nothing here at all", []),
        ("[3] then [3] again", [3]),
    ],
)
def test_grouped_markers_are_read_as_individual_citations(text: str, expected: list[int]) -> None:
    assert extract_citation_numbers(text) == expected


def test_grouped_marker_counts_as_two_references() -> None:
    """`[2, 4]` points at two separate pieces of evidence, so it counts twice."""
    assert count_citation_occurrences("see [2, 4]") == 2
    assert count_citation_occurrences("see [2] and [4]") == 2


def test_index_zero_is_not_a_citation() -> None:
    """`arr[0]` in prose must not invent a citation; numbering starts at 1."""
    assert extract_citation_numbers("the array arr[0] is empty") == []
    assert extract_citation_numbers("real citation [1]") == [1]


def test_code_spans_are_still_ignored() -> None:
    assert extract_citation_numbers("`code[7]` but real [2]") == [2]


def test_grouped_marker_resolves_to_both_sources() -> None:
    """The end-to-end consequence: both sources must appear in the citation list."""
    excerpts = [
        {"number": 1, "chunk_id": 10, "document_id": 1, "content": "one", "document_name": "a.md"},
        {"number": 2, "chunk_id": 11, "document_id": 1, "content": "two", "document_name": "a.md"},
        {"number": 4, "chunk_id": 13, "document_id": 1, "content": "four", "document_name": "a.md"},
    ]
    report = resolve_citations("Pool resources and scale elastically [2, 4].", excerpts)

    assert report.cited_numbers == [2, 4]
    assert sorted(c.number for c in report.citations) == [2, 4]
    assert report.invalid_numbers == []


def test_validation_notices_a_dropped_group_member() -> None:
    """Dropping half a group is a real citation loss, not a formatting detail."""
    source = "A [1]. B [2, 4]."
    assert validate_translation(source, "A [1]. B [2].").missing_citations == [4]
    assert validate_translation(source, "A [1]. B [2, 4].").missing_citations == []


def test_marker_substitution_removes_the_whole_group() -> None:
    """Read Aloud strips markers; a leftover ', 4]' would be spoken aloud."""
    assert CITATION_RE.sub("", "text [2, 4] more") == "text  more"


# ---------------------------------------------------------------------------
# Bug 3 - full-width citation brackets (the Groq fallback)
# ---------------------------------------------------------------------------
def test_fullwidth_brackets_are_normalised() -> None:
    """Groq emitted "【1】" (U+3010). ASCII-only parsing saw no citations at all."""
    assert normalize_citation_markers("used【1】 here") == "used[1] here"
    assert normalize_citation_markers("pooled［2, 4］") == "pooled[2, 4]"
    assert normalize_citation_markers("fullwidth digit【３】") == "fullwidth digit[3]"


def test_normalisation_leaves_ordinary_text_alone() -> None:
    """It must not behave like NFKC and rewrite unrelated characters."""
    assert normalize_citation_markers("plain [1] text") == "plain [1] text"
    assert normalize_citation_markers("café · naïve — dash") == "café · naïve — dash"
    assert normalize_citation_markers("") == ""


def test_normalisation_is_idempotent() -> None:
    once = normalize_citation_markers("used【1】 and ［2, 4］")
    assert normalize_citation_markers(once) == once


def test_fullwidth_markers_are_extracted() -> None:
    assert extract_citation_numbers("used【1】 and pooled［2, 4］") == [1, 2, 4]
    assert count_citation_occurrences("used【1】 and pooled［2, 4］") == 3


def test_fullwidth_markers_resolve_like_ascii_ones() -> None:
    """The end-to-end consequence: the same answer resolves the same way."""
    excerpts = [
        {"number": 1, "chunk_id": 10, "document_id": 1, "content": "one", "document_name": "a.md"},
        {"number": 2, "chunk_id": 11, "document_id": 1, "content": "two", "document_name": "a.md"},
    ]
    ascii_report = resolve_citations("Facts [1] and [2].", excerpts)
    wide_report = resolve_citations("Facts【1】 and 【2】.", excerpts)

    assert ascii_report.cited_numbers == wide_report.cited_numbers == [1, 2]
    assert len(wide_report.citations) == 2


def test_read_aloud_strips_fullwidth_markers() -> None:
    spoken = strip_citations_for_speech("Hardware is pooled【1】 and sold in increments【2, 4】.")
    assert "【" not in spoken and "】" not in spoken
    assert "[1]" not in spoken and "2, 4" not in spoken
    assert spoken == "Hardware is pooled and sold in increments."


# ---------------------------------------------------------------------------
# Bug 2 - truncation, across provider dialects
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "finish_reason, expected",
    [
        ("MAX_TOKENS", True),      # Gemini
        ("max_tokens", True),
        ("length", True),          # Groq / llama.cpp (OpenAI convention)
        ("LENGTH", True),
        ("STOP", False),
        ("stop", False),
        ("SAFETY", False),
        ("content_filter", False),
        ("", False),
        (None, False),
    ],
)
def test_truncation_is_normalised_across_providers(finish_reason, expected: bool) -> None:
    assert is_truncated(finish_reason) is expected


def test_response_reports_truncation_from_finish_reason() -> None:
    """A Groq/llama.cpp response is recognised without any provider-specific code."""
    groq_like = LLMResponse(text="cut off mid", provider="groq", model="m", finish_reason="length")
    assert groq_like.truncated is True

    complete = LLMResponse(text="finished", provider="groq", model="m", finish_reason="stop")
    assert complete.truncated is False


def test_explicit_token_usage_flag_wins() -> None:
    """Gemini sets the flag itself, because thinking tokens can consume the budget."""
    response = LLMResponse(
        text="x", provider="gemini", model="m",
        finish_reason="STOP", token_usage={"truncated": True},
    )
    assert response.truncated is True


def test_truncation_appears_in_the_trace_payload() -> None:
    response = LLMResponse(text="x", provider="groq", model="m", finish_reason="length")
    assert response.as_dict()["truncated"] is True
