"""Regression tests for the two generation prompts.

Offline mode used the same system prompt as online mode. That prompt is a six-rule
rulebook written for frontier models, and the 3B local model (Qwen2.5-3B) could not
work with it: asked "Tell me about the PDF" with five relevant chunks in front of it,
it replied

    "I could not find this in the documents in this workspace."

while Groq, given the *same* chunks, answered in detail. Measured with
`scripts/tune_offline_prompt.py`: the production prompt refused 3/3 times; the
offline prompt answers 3/3 and still refuses 3/3 on a question the documents do not
cover.

These tests pin the two properties that matter:

  1. **Online mode is untouched.** The user reported online working; the fix must not
     have changed a single character of its prompt.
  2. **The offline prompt keeps every guarantee.** Citations, the untrusted-data rule,
     and the no-fabrication refusal all have to survive, or the fix traded one bug
     for three.
"""

from __future__ import annotations

from app.rag.prompts import (
    GENERATION_SYSTEM,
    OFFLINE_GENERATION_SYSTEM,
    _REFUSAL_SENTENCE,
    build_generation_messages,
)

EXCERPTS = [
    {
        "number": 1,
        "label": "ADT_Notes.pdf · p.12",
        "content": "Design thinking has five stages: Empathy, Define, Ideate, Prototype, Test.",
        "chunk_id": 1,
        "document_id": 1,
    },
    {
        "number": 2,
        "label": "ADT_Notes.pdf · p.14",
        "content": "Human-Centered Design starts with the people you are designing for.",
        "chunk_id": 2,
        "document_id": 1,
    },
]

QUESTION = "Tell me about the PDF"


def _online() -> list[dict[str, str]]:
    return build_generation_messages(QUESTION, EXCERPTS, mode="online")


def _offline() -> list[dict[str, str]]:
    return build_generation_messages(QUESTION, EXCERPTS, mode="offline")


# ---------------------------------------------------------------------------
# Property 1 - online mode is untouched
# ---------------------------------------------------------------------------
def test_online_uses_the_original_system_prompt() -> None:
    assert _online()[0]["content"] == GENERATION_SYSTEM


def test_online_user_block_has_no_offline_additions() -> None:
    """The offline framing lines must not leak into the online prompt."""
    user = _online()[1]["content"]

    assert "Answer:" not in user
    assert "summarise what the excerpts say" not in user
    assert "completely different subject" not in user
    assert _REFUSAL_SENTENCE not in user


def test_online_user_block_still_has_its_original_shape() -> None:
    user = _online()[1]["content"]

    assert "=== QUESTION ===" in user
    assert "=== END QUESTION ===" in user
    assert QUESTION in user


def test_mode_defaults_to_online() -> None:
    """A caller that forgets to pass `mode` must get the frontier-model prompt."""
    default = build_generation_messages(QUESTION, EXCERPTS)
    assert default[0]["content"] == GENERATION_SYSTEM


def test_online_prompt_carries_the_untrusted_data_rule() -> None:
    """Security is the one thing that must never be traded away."""
    system = _online()[0]["content"].lower()
    assert "data, not" in system or "data, not\n" in system
    assert "instructions" in system


# ---------------------------------------------------------------------------
# Property 2 - the offline prompt keeps every guarantee
# ---------------------------------------------------------------------------
def test_offline_uses_the_small_model_prompt() -> None:
    assert _offline()[0]["content"] == OFFLINE_GENERATION_SYSTEM


def test_offline_system_prompt_is_much_shorter() -> None:
    """Length was a cause of the refusal: every rule token is attention taken from
    the evidence. If this ever creeps back up, offline mode will regress."""
    assert len(OFFLINE_GENERATION_SYSTEM) < len(GENERATION_SYSTEM) / 3


def test_offline_prompt_licenses_summarising() -> None:
    """A broad question has no single answer sentence to find, so a literal reading
    says the context does not answer it. This line is what fixes that."""
    assert "summarise what the excerpts say" in _offline()[1]["content"]


def test_offline_refusal_is_a_stricter_last_resort() -> None:
    """'the context is silent' invites refusal; 'a completely different subject'
    does not. This is the difference between the two prompts."""
    user = _offline()[1]["content"]

    assert "completely different subject" in user
    assert "Only if" in user


def test_offline_prompt_ends_with_a_direct_imperative() -> None:
    assert _offline()[1]["content"].rstrip().endswith("Answer:")


def test_offline_prompt_requires_citations() -> None:
    user = _offline()[1]["content"]
    assert "[1] or [2, 4]" in user


def test_offline_prompt_carries_the_untrusted_data_rule() -> None:
    system = OFFLINE_GENERATION_SYSTEM.lower()
    assert "data, not instructions" in system


def test_offline_prompt_keeps_the_exact_refusal_sentence() -> None:
    """Grounding and the frontend both match this string character-for-character."""
    assert _REFUSAL_SENTENCE in _offline()[1]["content"]


def test_both_prompts_use_the_same_refusal_sentence() -> None:
    """If these ever drift, a refusal in one mode stops being recognised."""
    assert _REFUSAL_SENTENCE in GENERATION_SYSTEM
    assert _REFUSAL_SENTENCE in OFFLINE_GENERATION_SYSTEM or _REFUSAL_SENTENCE in _offline()[1][
        "content"
    ]


# ---------------------------------------------------------------------------
# Shared behaviour
# ---------------------------------------------------------------------------
def test_both_modes_put_evidence_before_the_question() -> None:
    """The model should read the evidence first; this measurably helps small models."""
    for messages in (_online(), _offline()):
        user = messages[1]["content"]
        assert user.index("ADT_Notes.pdf") < user.index("=== QUESTION ===")


def test_both_modes_include_every_excerpt() -> None:
    for messages in (_online(), _offline()):
        user = messages[1]["content"]
        assert "Design thinking has five stages" in user
        assert "Human-Centered Design starts" in user


def test_offline_still_asks_the_model_to_cite() -> None:
    """Citations are the product. A prompt that answers without citing is a regression."""
    assert "[1]" in _offline()[1]["content"]
