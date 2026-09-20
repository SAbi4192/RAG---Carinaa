"""Regression tests for translation budgeting and the degeneration guard.

Two findings, both from the offline investigation:

1. **The output budget ignored the target script.** It was derived from the source's
   character count alone. A 552-character English answer became ~1183 characters of
   Tamil, and the old budget stopped the model mid-sentence.

2. **A degenerate translation passed every check.** When the 3B local model runs
   away it produces a 3.8x expansion mixing English words into Tamil and looping -
   and because every citation and number still matched, the existing checks all
   passed and the gibberish would have been shown to the user. `length_ratio` was
   already computed; nothing enforced it.

Note on what these tests do NOT claim: the local model does not reliably emit an
end-of-sequence token for long Indic translations. Measured, it consumed every
budget from 1024 to 2048 tokens with the output growing linearly. So the goal is
not "never truncated" - it is "never *silently* wrong", which is what the warning
and the ratio guard together provide.
"""

from __future__ import annotations

import pytest

from app.features.languages import LANGUAGES, get_language, translation_token_budget
from app.features.validation import (
    MAX_TRANSLATION_LENGTH_RATIO,
    MIN_TRANSLATION_LENGTH_RATIO,
    validate_translation,
)


# ---------------------------------------------------------------------------
# Budget sizing
# ---------------------------------------------------------------------------
def test_indic_languages_get_more_tokens_than_latin_ones() -> None:
    """The whole point: non-Latin scripts cost more tokens for the same content."""
    source = "x" * 600

    tamil = translation_token_budget(source, "ta")
    italian = translation_token_budget(source, "it")
    english = translation_token_budget(source, "en")

    assert tamil > italian >= english


def test_every_language_gets_a_usable_floor() -> None:
    """A one-word answer still needs room to become a sentence."""
    for language in LANGUAGES:
        assert translation_token_budget("short answer [1]", language.code) >= 1024


def test_budget_is_capped_so_it_cannot_outgrow_a_local_context() -> None:
    """A 12,000-character answer must not ask for an unbounded budget."""
    for language in LANGUAGES:
        assert translation_token_budget("x" * 12000, language.code) <= 4096


def test_tamil_budget_actually_covers_the_case_that_failed() -> None:
    """552 characters of English became ~1183 characters of Tamil before the fix
    stopped it at 1024 tokens."""
    assert translation_token_budget("x" * 552, "ta") >= 1300


def test_budget_is_monotonic_in_source_length() -> None:
    assert translation_token_budget("x" * 300, "ta") <= translation_token_budget("x" * 900, "ta")


def test_unknown_language_falls_back_safely() -> None:
    """`get_language` falls back to English; the budget must not raise."""
    assert get_language("xx").code == "en"
    assert translation_token_budget("some text", "xx") >= 1024


# ---------------------------------------------------------------------------
# Degeneration guard
# ---------------------------------------------------------------------------
def test_a_runaway_translation_is_rejected() -> None:
    """The real failure: 552 chars in, 2082 chars out (3.8x)."""
    source = "A detailed answer about virtualization. [1] " * 12
    runaway = "மெய்நிகராக்கம் " * 160  # ~2082 chars of looping Tamil

    result = validate_translation(source, runaway)

    assert result.passed is False
    assert result.length_ratio > MAX_TRANSLATION_LENGTH_RATIO
    assert any("repeated itself" in issue for issue in result.issues)


def test_a_plausible_translation_is_accepted() -> None:
    """A faithful Tamil rendering lands near 1.7x and must not be flagged."""
    source = "A detailed answer about virtualization. [1] " * 12
    good = "மெய்நிகராக்கம் பற்றிய விரிவான பதில். [1] " * 12

    result = validate_translation(source, good)

    assert result.length_ratio <= MAX_TRANSLATION_LENGTH_RATIO
    assert not any("repeated itself" in issue for issue in result.issues)


def test_a_truncated_translation_is_rejected_when_content_was_lost() -> None:
    """A translation a fraction of the source length dropped most of the answer."""
    source = "A detailed answer about virtualization and hypervisors. [1] " * 12
    tiny = "மெய்நிகராக்கம். [1]"

    result = validate_translation(source, tiny)

    assert result.passed is False
    assert any("dropped" in issue or "short" in issue for issue in result.issues)


def test_short_sources_are_exempt_from_the_minimum_ratio() -> None:
    """Short texts vary wildly across scripts; the ratio is not meaningful there."""
    source = "Yes, that is correct. [1]"
    short = "ஆம். [1]"

    result = validate_translation(source, short)

    assert not any("dropped" in issue for issue in result.issues)


def test_the_guard_does_not_mask_real_problems() -> None:
    """Adding a length check must not weaken the citation and number checks."""
    source = "A long answer about virtualization that cites two sources. [1] [2] " * 8
    # A plausible length, but citation [2] was dropped.
    output = "மெய்நிகராக்கம் பற்றிய நீண்ட பதில். [1] " * 8

    result = validate_translation(source, output)

    assert result.passed is False
    assert 2 in result.missing_citations


def test_boundaries_are_loose_enough_to_be_usable() -> None:
    """If these ever tighten to something a real translation cannot satisfy, every
    offline translation would be rejected and the feature would be dead."""
    assert MAX_TRANSLATION_LENGTH_RATIO >= 3.0
    assert MIN_TRANSLATION_LENGTH_RATIO <= 0.35


@pytest.mark.parametrize("ratio, expected", [(1.0, True), (2.5, True), (4.0, False)])
def test_ratio_boundary_behaviour(ratio: float, expected: bool) -> None:
    source = "A reasonably long grounded answer with a citation. [1] " * 10
    output = "x" * int(len(source) * ratio)

    result = validate_translation(source, output)

    assert (result.length_ratio <= MAX_TRANSLATION_LENGTH_RATIO) is expected
