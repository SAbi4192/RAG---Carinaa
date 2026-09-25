"""Cloud-provider identity must never reach the browser.

THE RULE (Phases 6, 7, 31, 32, 36)
----------------------------------
The backend has a provider chain (primary remote engine, backup remote engine,
local model) and keeps that truth in its logs and database. Nothing in a
browser-facing payload may name the cloud vendor, its model ID, or a filesystem
path. "Hiding the label with CSS" is not acceptable because the browser would
still receive the string.

This module checks the rule from BOTH ends:
  1. the source-level sanitizer functions, so the boundary is correct;
  2. a static scan of the frontend sources, so no hardcoded vendor name crept
     back into the shipped UI.

If a future change reintroduces a vendor name into a public payload, one of
these tests fails at the point of the leak, not three screens away.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.sanitize import (
    ENGINE_FALLBACK,
    ENGINE_PRIMARY,
    local_model_name,
    public_provenance,
    public_provider_label,
    public_trace_summary,
    sanitize_trace_data,
)

# Tokens that must never appear in a public payload or in the frontend bundle.
FORBIDDEN = [
    "groq",
    "Gemini",
    "openai/gpt-oss",
    "gpt-oss-120b",
    "gemini-",
]


# ---------------------------------------------------------------------------
# 1. The provenance sanitizer
# ---------------------------------------------------------------------------
def test_cloud_provider_collapses_to_role_not_vendor() -> None:
    provider, model = public_provenance("groq", "openai/gpt-oss-120b")
    assert provider == "remote"
    assert model == ""
    assert "groq" not in provider.lower()
    assert "gpt-oss" not in model.lower()


def test_gemini_provenance_is_also_scrubbed() -> None:
    provider, model = public_provenance("gemini", "gemini-2.5-flash")
    assert provider == "remote"
    assert model == ""


def test_local_model_keeps_its_real_name() -> None:
    """The local model is the user's own configuration, not vendor data."""
    provider, model = public_provenance("local", "Some Other GGUF")
    assert provider == "local"
    assert model == "Some Other GGUF"


def test_extractive_failsafe_marker_is_honest() -> None:
    # "no model was used" is a product promise, so the marker survives.
    provider, model = public_provenance("local", "extractive")
    assert (provider, model) == ("local", "extractive")


def test_provider_labels_are_role_based() -> None:
    assert public_provider_label(provider="groq", model="x") == ENGINE_PRIMARY
    assert (
        public_provider_label(provider="gemini", model="x", used_fallback=True)
        == ENGINE_FALLBACK
    )
    for label in (
        public_provider_label(provider="groq", model="x"),
        public_provider_label(provider="gemini", model="x", used_fallback=True),
    ):
        assert "groq" not in label.lower()
        assert "gemini" not in label.lower()


# ---------------------------------------------------------------------------
# 2. Trace event sanitization
# ---------------------------------------------------------------------------
def test_generation_event_loses_vendor_and_model() -> None:
    data = {
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "role": "primary",
        "temperature": 0.2,
        "tokens": 180,
    }
    clean = sanitize_trace_data("llm_generation", data)
    assert clean["provider"] == "remote"
    assert "model" not in clean
    # Useful technical detail survives; only vendor identity is removed.
    assert clean["role"] == "primary"
    assert clean["tokens"] == 180


def test_fallback_reason_is_generic_on_the_wire() -> None:
    clean = sanitize_trace_data(
        "llm_generation",
        {"provider": "gemini", "fallback_reason": "Groq returned 429."},
    )
    assert "429" not in clean["fallback_reason"]
    assert "groq" not in clean["fallback_reason"].lower()


def test_free_text_error_is_scrubbed() -> None:
    clean = sanitize_trace_data(
        "llm_generation",
        {"provider": "groq", "error": "Gemini rate limited, falling back to Groq."},
    )
    text = clean["error"].lower()
    assert "gemini" not in text
    assert "groq" not in text


def test_local_event_keeps_model_name_but_not_path() -> None:
    clean = sanitize_trace_data(
        "llm_generation",
        {
            "provider": "local",
            "model": "My Custom Model",
            # A reason that leaks an absolute path must be genericised.
            "reason": r"No file at D:\Academic\private\models\secret.gguf",
        },
    )
    assert clean["model"] == "My Custom Model"
    assert "\\" not in clean["reason"] and ".gguf" not in clean["reason"]


def test_non_provider_stage_reason_is_preserved() -> None:
    """Useful technical detail on stages that never carry vendor identity."""
    data = {"enabled": False, "reason": "re-ranking is disabled in settings"}
    clean = sanitize_trace_data("reranking", data)
    assert clean["reason"] == "re-ranking is disabled in settings"


def test_public_trace_summary_scrubs_every_stage() -> None:
    trace = {
        "trace_id": "t1",
        "total_ms": 1200,
        "stages": [
            {
                "seq": 0,
                "stage": "llm_generation",
                "status": "ok",
                "duration_ms": 900,
                "data": {"provider": "groq", "model": "openai/gpt-oss-120b", "role": "primary"},
            },
            {
                "seq": 1,
                "stage": "vector_search",
                "status": "ok",
                "duration_ms": 30,
                "data": {"candidates_returned": 8},
            },
        ],
    }
    public = public_trace_summary(trace)
    blob = str(public).lower()
    assert "groq" not in blob
    assert "gpt-oss" not in blob
    # The useful numbers survive.
    assert public["stages"][1]["data"]["candidates_returned"] == 8


def test_local_model_name_never_leaks_a_path() -> None:
    name = local_model_name()
    assert "/" not in name and "\\" not in name
    assert not name.endswith(".gguf")


# ---------------------------------------------------------------------------
# 3. Static scan of the frontend source
# ---------------------------------------------------------------------------
FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

# Vendor identifiers that must not appear in shipped UI source. Matching is
# case-sensitive on the model IDs (they are always lowercase in APIs) but
# case-insensitive on bare provider names.
_UI_FORBIDDEN = [
    r"\bGroq\b",
    r"\bGROQ\b",
    r"\bGemini\b",
    r"\bGEMINI\b",
    r"openai/gpt-oss",
    r"gemini-\d",
    r"Qwen2\.5-3B",
]

# A comment is not a user-facing string, but we still forbid the tokens in the
# runtime code. These directory names hold test/example scripts only.
_SKIP_DIRS = {"node_modules", "__pycache__", ".venv"}


def _frontend_source_files() -> list[Path]:
    files: list[Path] = []
    for path in FRONTEND_SRC.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if _SKIP_DIRS & set(path.parts):
            continue
        files.append(path)
    return files


@pytest.mark.parametrize("pattern", _UI_FORBIDDEN)
def test_frontend_source_has_no_vendor_identity(pattern: str) -> None:
    """The UI bundle must not contain cloud vendor names at all.

    This is the Phase 36 global search encoded as a test: it is what stops the
    vendor name reappearing in some string nobody looked at. Comments are
    stripped first because the ban is on user-facing content, and a code comment
    explaining WHY a provider is hidden is legitimate.
    """
    regex = re.compile(pattern)
    offenders: list[str] = []
    for path in _frontend_source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        # Drop line comments and block comments before matching.
        text = re.sub(r"//.*", "", text)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        for match in regex.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(FRONTEND_SRC)}:{line} -> {match.group(0)!r}")
    assert not offenders, (
        "frontend source contains cloud-provider identity that the UI must never "
        f"ship (pattern {pattern!r}):\n  " + "\n  ".join(offenders)
    )
