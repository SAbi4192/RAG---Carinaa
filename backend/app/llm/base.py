"""
LLM provider interface.

WHY AN ADAPTER (spec section 13)
--------------------------------
    RAG pipeline  ->  LLM adapter  ->  provider

The RAG pipeline must not know whether it is talking to Gemini, Groq or a local
GGUF file. It hands over a list of messages and gets back text. Everything
provider-specific - auth headers, JSON shapes, retry semantics, model naming -
stays behind this interface.

Two concrete payoffs for this project:

  1. Offline mode becomes a *routing decision*, not a rewrite. The same pipeline
     runs; only the provider behind the adapter changes.
  2. Swapping a deprecated model ID is a configuration change in one file, not a
     search-and-replace across the codebase.

THE HONESTY RULE
----------------
`LLMResponse.provider` and `.used_fallback` are always populated with what
ACTUALLY served the request. If Gemini failed and Groq answered, the user sees
"Gemini · Fallback". We never present a fallback as if it were the primary, and we
never silently switch from Offline to Online.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Truncation, normalised across provider dialects
# ---------------------------------------------------------------------------
# Every provider signals "I stopped because I ran out of output budget" with a
# different word, and none of them raise an error when it happens:
#
#   Gemini   finishReason: MAX_TOKENS
#   Groq     finish_reason: "length"      (OpenAI-compatible)
#   llama.cpp finish_reason: "length"
#
# A truncated answer is dangerous precisely because it looks like a complete one.
# In this project a cut-off translation once dropped a citation marker and a set of
# figures, and nothing in the stack noticed. So we normalise the vocabulary once,
# here, and every provider reports through it.
_TRUNCATION_FINISH_REASONS = frozenset(
    {"MAX_TOKENS", "LENGTH", "MAX_OUTPUT_TOKENS", "TOKEN_LIMIT", "CONTENT_LENGTH"}
)


def is_truncated(finish_reason: str | None) -> bool:
    """True when a provider's finish reason means 'hit the output limit'."""
    return (finish_reason or "").strip().upper() in _TRUNCATION_FINISH_REASONS


@dataclass
class LLMResponse:
    """The result of one generation call."""

    text: str
    provider: str
    model: str
    latency_ms: int = 0

    # Honest provenance
    used_fallback: bool = False
    fallback_reason: str = ""
    primary_attempted: str = ""

    token_usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    is_extractive_failsafe: bool = False

    @property
    def truncated(self) -> bool:
        """True when the provider stopped before it had finished.

        Callers use this to decide whether output can be trusted as *complete* -
        a shortening that was cut off cannot be called a verified compression, and
        a translation that was cut off may be missing citations.
        """
        explicit = self.token_usage.get("truncated")
        if explicit is not None:
            return bool(explicit)
        return is_truncated(self.finish_reason)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "used_fallback": self.used_fallback,
            "fallback_reason": self.fallback_reason,
            "primary_attempted": self.primary_attempted,
            "token_usage": self.token_usage,
            "finish_reason": self.finish_reason,
            "truncated": self.truncated,
            "is_extractive_failsafe": self.is_extractive_failsafe,
        }


@dataclass
class ProviderStatus:
    """What the Settings page shows. Never contains credentials."""

    name: str
    label: str
    role: str            # primary | fallback | offline
    configured: bool
    available: bool
    model: str = ""
    reason: str = ""
    modes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "role": self.role,
            "configured": self.configured,
            "available": self.available,
            "model": self.model,
            "reason": self.reason,
            "modes": list(self.modes),
        }


class LLMError(Exception):
    """Raised by a provider when it cannot serve a request."""

    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class RateLimitedError(LLMError):
    def __init__(self, message: str = "Rate limited by the provider."):
        super().__init__(message, retryable=True, status_code=429)


class ProviderUnavailableError(LLMError):
    def __init__(self, message: str = "Provider unavailable."):
        super().__init__(message, retryable=True, status_code=503)


class ProviderConfigError(LLMError):
    def __init__(self, message: str = "Provider is not configured."):
        super().__init__(message, retryable=False, status_code=401)


@runtime_checkable
class LLMProvider(Protocol):
    """Every provider implements exactly this."""

    name: str
    label: str

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Produce a completion. Raises LLMError on failure."""
        ...

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas as the provider produces them.

        This is REAL streaming: each yielded string is a fragment the provider
        actually sent, in order. It is never a complete answer chopped up after
        the fact, because a fake stream would make the UI's central claim - that
        you are watching the answer being produced - untrue.
        """
        ...

    async def list_models(self) -> list[str]:
        """Live model list from the provider, so IDs never go stale."""
        ...

    def status(self) -> ProviderStatus:
        ...


async def iter_sse_data(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    """Yield the `data:` payload of each Server-Sent Event line.

    Both Groq (OpenAI-compatible) and Gemini stream as SSE. They differ only in the
    JSON inside the event, not in the framing, so the framing is parsed once here.

    A line is only yielded when it carries a payload; comments (":" heartbeat),
    blank separators and the terminal "data: [DONE]" sentinel are filtered out so
    every caller receives plain JSON.
    """
    async for raw in lines:
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        yield payload


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def approx_tokens(text: str) -> int:
    """Rough token count for reporting when a provider does not return usage."""
    return max(1, len(text) // 4)


def flatten_messages(messages: list[dict[str, str]]) -> tuple[str, str]:
    """Split a message list into (system, user) for APIs that need it separately."""
    system_parts: list[str] = []
    user_parts: list[str] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if role == "system":
            system_parts.append(content)
        else:
            user_parts.append(content)
    return "\n\n".join(system_parts), "\n\n".join(user_parts)


class Stopwatch:
    """Small helper so every provider reports latency the same way."""

    def __enter__(self) -> "Stopwatch":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: Any) -> None:
        self.elapsed_ms = int((time.perf_counter() - self._start) * 1000)
