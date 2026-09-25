"""
Public (browser-facing) engine provenance.

WHY THIS FILE EXISTS
--------------------
The backend has always known WHICH provider and model actually served every answer,
and it will keep storing that internally for developer logs and analytics. The
browser, however, does not need cloud vendor identity: it needs to know whether an
answer came from the remote answer engine, whether a fallback was used, and (for
local mode) the actual configured model name.

This module is the single sanitisation boundary between internal provenance and
public payloads. Every route that sends provider or model information to the client
passes it through here, so the rule "the frontend never learns which cloud vendor
answered" is enforced in one auditable place rather than at twenty call sites.

Rules:
  * cloud providers (anything that is not the local model) collapse to a generic
    "remote answer engine" label; no vendor name, no cloud model ID;
  * the local model keeps its real name (the user configured it themselves), but
    never an absolute filesystem path;
  * the extractive fail-safe keeps its honest marker, because "no model was used"
    is a product promise, not vendor information.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings

# Values of `provider` that are allowed to keep their identity on the wire.
_LOCAL_PROVIDERS = frozenset({"local"})
_EXTRACTIVE_MODEL = "extractive"

ENGINE_PRIMARY = "Remote answer engine"
ENGINE_FALLBACK = "Remote answer engine · Fallback"
ENGINE_LOCAL = "Local model"
ENGINE_EXTRACTIVE = "Extractive (no model)"


def local_model_name() -> str:
    """The configured local model's name, never a path, never invented.

    Resolution order (see app.llm.local.LocalProvider.resolved_label):
      explicit LOCAL_MODEL_LABEL -> GGUF embedded metadata -> "Configured local model"
    """
    from app.llm.local import get_local_provider

    return get_local_provider().resolved_label()


def public_provenance(provider: str, model: str) -> tuple[str, str]:
    """Sanitised (provider, model) pair for any browser-facing payload.

    The stored answer keeps its real provenance in the database; only what leaves
    the server through a public schema passes through here.
    """
    provider = (provider or "").strip().lower()
    model = (model or "").strip()

    if model == _EXTRACTIVE_MODEL or provider == _EXTRACTIVE_MODEL:
        return ("local", _EXTRACTIVE_MODEL)
    if provider in _LOCAL_PROVIDERS:
        # The local model name is the user's own configuration; it is not vendor
        # data. `extractive` above already covers the failsafe.
        return ("local", model or local_model_name())
    # A role, never a vendor: "remote" tells the UI which CLASS answered so it can
    # show the generic engine label; the empty model means no cloud model ID ships.
    return ("remote", "")


def public_provider_label(
    *,
    provider: str,
    model: str = "",
    used_fallback: bool = False,
) -> str:
    """The generic engine label the UI shows in place of "Groq" / "Gemini"."""
    provider = (provider or "").strip().lower()
    if (model or "").strip() == _EXTRACTIVE_MODEL:
        return ENGINE_EXTRACTIVE
    if provider in _LOCAL_PROVIDERS:
        return f"{ENGINE_LOCAL} · {local_model_name()}"
    return ENGINE_FALLBACK if used_fallback else ENGINE_PRIMARY


def _scrub_text(value: str) -> str:
    """Replace vendor names and cloud model IDs inside free-text with generic wording.

    Provider error strings ("Groq returned 429", "falling back to Gemini",
    "model openai/gpt-oss-120b was rejected") are useful in developer logs and are
    kept there untouched. On the wire they become role-based descriptions, so no
    vendor identity leaks through a message field.

    Configured cloud model IDs are scrubbed too, because the vendor name alone is
    not the secret - "openai/gpt-oss-120b" identifies the same engine as "Groq"
    does, and error strings often carry only the model. The list is read from the
    live configuration, so it stays correct when the operator changes models.
    """
    import re

    text = value
    lowered = text.lower()

    vendor_targets: list[tuple[str, str]] = [
        ("groq", "the remote answer engine"),
        ("gemini", "the backup answer engine"),
    ]
    # Cloud model IDs from the live config (not the local GGUF's name, which is
    # allowed, and not the embedding/rerank models, which run on-device).
    for raw in (settings.groq_model, settings.gemini_model, settings.gemini_models,
                settings.groq_models if hasattr(settings, "groq_models") else ""):
        for model_id in str(raw or "").split(","):
            model_id = model_id.strip()
            if model_id:
                vendor_targets.append((model_id.lower(), "a remote model"))

    for needle, generic in vendor_targets:
        if needle in lowered:
            text = re.sub(
                rf"(?i){re.escape(needle)}(?:[a-z0-9_.:/-]*[a-z0-9])?", generic, text
            )
    return text


# Trace event data keys that must never reach the browser with vendor identity.
# Applied ONLY to events that carry provider identity (stage `llm_generation`, or a
# data["provider"] that is not "local"). Other stages' `reason` values - "re-ranking
# is disabled in settings", "keyword-only retrieval needs no embedding" - are honest
# technical information the UI must keep showing.
_REDACT_WHEN_PROVIDER = {
    "provider": "remote",
    "model": None,  # None means: drop the key
    "fallback_reason": "The primary answer engine was unavailable.",
    "action": None,
    "reason": "The primary answer engine was unavailable.",
}


def sanitize_trace_data(stage: str, data: dict[str, Any]) -> dict[str, Any]:
    """Strip cloud-provider identity from one trace event's data dict.

    Useful technical values (durations, counts, scores, roles, modes, skipped
    reasons like "re-ranking is disabled") are kept verbatim. Only vendor names
    and cloud model IDs are removed or replaced with generic role wording.

    A local-model event is exempt from the provider redaction (its model name is
    user-configured and allowed), but its strings still pass through `_scrub_text`
    so a path or a stray vendor name cannot ride along inside e.g. an error string.
    """
    if not isinstance(data, dict):
        return data

    provider = str(data.get("provider") or "").strip().lower()
    is_local = provider in _LOCAL_PROVIDERS
    # The stages that carry provider identity. Other stages' data - retrieval
    # counts, rerank notes, skip reasons like "re-ranking is disabled" - is honest
    # technical information and is never redacted, only scrubbed for stray strings.
    carries_cloud_provider = (stage == "llm_generation" or bool(provider)) and not is_local

    if not carries_cloud_provider:
        out: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                out[key] = _scrub_deep(value)
            elif isinstance(value, str):
                out[key] = _scrub_text(value)
            else:
                out[key] = value
        # A local event's `reason` can contain an absolute model path; never send paths.
        if is_local and isinstance(out.get("reason"), str):
            out["reason"] = "The local model is unavailable."
        return out

    out = {}
    for key, value in data.items():
        if key in _REDACT_WHEN_PROVIDER:
            replacement = _REDACT_WHEN_PROVIDER[key]
            if replacement is None:
                continue
            out[key] = replacement
            continue
        if key in ("models_tried", "skipped_models"):
            # Model IDs the chain tried - pure cloud identity.
            continue
        if key in ("message", "error", "note"):
            out[key] = _scrub_text(str(value))
            continue
        if isinstance(value, (dict, list)):
            out[key] = _scrub_deep(value)
        elif isinstance(value, str):
            out[key] = _scrub_text(value)
        else:
            out[key] = value
    return out


def sanitize_event_dict(stage: str, event: dict[str, Any]) -> dict[str, Any]:
    """Public form of one already-serialised trace event dict (`TraceEvent.as_dict`
    or an SSE `stage` frame). Returns a copy; the recorder's own data is untouched,
    so the database still keeps real provenance for developers.
    """
    if not isinstance(event, dict):
        return event
    out = dict(event)
    if isinstance(out.get("data"), dict):
        out["data"] = sanitize_trace_data(stage, out["data"])
    return out


def public_trace_summary(trace_dict: dict[str, Any]) -> dict[str, Any]:
    """Public form of `TraceRecorder.summary()` / a stored trace payload."""
    if not isinstance(trace_dict, dict):
        return trace_dict
    out = dict(trace_dict)
    stages = []
    for stage in out.get("stages", []) or []:
        if isinstance(stage, dict):
            stages.append(sanitize_event_dict(str(stage.get("stage") or ""), stage))
    out["stages"] = stages
    return out


def _scrub_deep(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_deep(v) for v in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def public_engines() -> dict[str, Any]:
    """The settings-page view of available engines, expressed as roles.

    Replaces the historical payload that named providers and cloud model IDs.
    Availability flags stay real: the UI must still tell the user when nothing
    is configured.
    """
    from app.llm.adapter import get_llm_adapter

    adapter = get_llm_adapter()
    statuses = {status.name: status for status in adapter.provider_statuses()}
    modes = adapter.mode_status()

    def _configured(name: str) -> bool:
        status = statuses.get(name)
        return bool(status and status.configured)

    return {
        "engines": [
            {
                "role": "primary",
                "label": "Primary remote engine",
                "configured": _configured("groq"),
                "available": bool(statuses.get("groq") and statuses["groq"].available),
            },
            {
                "role": "fallback",
                "label": "Backup remote engine",
                "configured": _configured("gemini"),
                "available": bool(statuses.get("gemini") and statuses["gemini"].available),
            },
            {
                "role": "offline",
                "label": "Local model",
                "name": local_model_name() if adapter.local.model_present else None,
                "configured": adapter.local.model_present,
                "available": bool(modes["offline"]["available"]),
                "reason": _scrub_text(str(modes["offline"].get("reason") or "")),
                "guarantee": modes["offline"].get("guarantee", ""),
            },
        ],
        "modes": {
            "online": {
                "available": bool(modes["online"]["available"]),
                "reason": _scrub_text(str(modes["online"].get("reason") or "")),
            },
            "offline": {
                "available": bool(modes["offline"]["available"]),
            },
            "default_mode": modes.get("default_mode", settings.default_ai_mode),
        },
        "note": (
            "Answer engines are reported by role. Cloud implementation identity is "
            "kept on the server; the local model name is shown as configured."
        ),
    }
