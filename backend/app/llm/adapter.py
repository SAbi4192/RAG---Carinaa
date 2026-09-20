"""
The LLM adapter - the only place provider routing is decided.

    RAG pipeline  ->  LLM adapter  ->  provider

ONLINE MODE
-----------
    RAG context -> LLM adapter -> Gemini
                                    | failure (rate limit, outage, bad key)
                                    v
                                  Groq

Gemini is primary, Groq is fallback, and the fallback is ALWAYS disclosed. The
`LLMResponse` carries `used_fallback` and `fallback_reason`, which the UI renders
as "Groq · Fallback" instead of quietly showing "Groq".

OFFLINE MODE
------------
    RAG context -> LLM adapter -> local llama.cpp  (and nothing else)

There is no fallback to an online provider here. This is the single most important
guarantee in the product (spec section 15), so the code enforces it structurally:
`_generate_offline` has no reference to Gemini or Groq at all. It is not that we
remember not to fall back - it is that there is nothing to fall back to.

If the local model cannot load, we either raise a clear error or return a
clearly-labelled extractive answer. Both are honest. Neither goes online.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.errors import (
    AllProvidersFailed,
    LocalModelUnavailable,
    ProviderNotConfigured,
    ProviderRateLimited,
)
from app.core.logging import get_logger
from app.llm.base import (
    LLMError,
    LLMResponse,
    ProviderConfigError,
    ProviderStatus,
    RateLimitedError,
)
from app.llm.gemini import GeminiProvider
from app.llm.groq import GroqProvider
from app.llm.local import get_local_provider
from app.rag.trace import TraceRecorder

logger = get_logger(__name__)

Mode = str  # "online" | "offline"

# A single retry on a transient failure, with a short backoff. Providers have
# brief blips; hammering them makes rate limiting worse.
_RETRY_DELAY_SECONDS = 1.5


class LLMAdapter:
    """Provider-agnostic generation."""

    def __init__(self) -> None:
        self.gemini = GeminiProvider()
        self.groq = GroqProvider()
        self.local = get_local_provider()

    # =====================================================================
    # Public entry point
    # =====================================================================
    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        mode: str = "online",
        temperature: float | None = None,
        max_tokens: int | None = None,
        trace: TraceRecorder | None = None,
    ) -> LLMResponse:
        """Generate a completion, honouring the selected AI mode exactly."""
        normalized_mode = (mode or "online").strip().lower()
        if normalized_mode not in ("online", "offline"):
            raise ValueError(f"Unknown AI mode '{mode}'.")

        if normalized_mode == "offline":
            return await self._generate_offline(
                messages, temperature=temperature, max_tokens=max_tokens, trace=trace
            )
        return await self._generate_online(
            messages, temperature=temperature, max_tokens=max_tokens, trace=trace
        )

    # =====================================================================
    # ONLINE: Gemini -> Groq
    # =====================================================================
    async def _generate_online(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None,
        max_tokens: int | None,
        trace: TraceRecorder | None,
    ) -> LLMResponse:
        attempts: list[str] = []
        primary_error: LLMError | None = None

        # ---------------------------------------------------------- Gemini
        if self.gemini.configured:
            attempts.append("gemini")
            try:
                response = await self._with_retry(
                    self.gemini.generate, messages, temperature, max_tokens
                )
                if trace:
                    trace.add(
                        "llm_generation",
                        duration_ms=response.latency_ms,
                        data={
                            "mode": "online",
                            "provider": "gemini",
                            "model": response.model,
                            "role": "primary",
                            "used_fallback": False,
                            "token_usage": response.token_usage,
                            "finish_reason": response.finish_reason,
                        },
                    )
                return response
            except LLMError as exc:
                primary_error = exc
                logger.warning("Gemini failed, trying Groq: %s", exc)
                if trace:
                    trace.add(
                        "llm_generation",
                        status="error",
                        duration_ms=0,
                        data={
                            "mode": "online",
                            "provider": "gemini",
                            "role": "primary",
                            "error": exc.__class__.__name__,
                            "message": str(exc),
                            "action": "falling back to Groq",
                        },
                    )
        else:
            if trace:
                trace.add(
                    "llm_generation",
                    status="skipped",
                    duration_ms=0,
                    data={
                        "mode": "online",
                        "provider": "gemini",
                        "role": "primary",
                        "reason": "GEMINI_API_KEY is not configured on the server",
                    },
                )

        # ------------------------------------------------------------ Groq
        if self.groq.configured:
            attempts.append("groq")
            try:
                response = await self._with_retry(
                    self.groq.generate, messages, temperature, max_tokens
                )
                # Disclose the fallback. Never hide it.
                response.used_fallback = True
                response.primary_attempted = "gemini" if primary_error else "none"
                response.fallback_reason = (
                    str(primary_error) if primary_error else "Gemini was not configured."
                )
                if trace:
                    trace.add(
                        "llm_generation",
                        duration_ms=response.latency_ms,
                        data={
                            "mode": "online",
                            "provider": "groq",
                            "model": response.model,
                            "role": "fallback",
                            "used_fallback": True,
                            "primary_attempted": response.primary_attempted,
                            "fallback_reason": response.fallback_reason,
                            "token_usage": response.token_usage,
                            "finish_reason": response.finish_reason,
                        },
                    )
                return response
            except LLMError as exc:
                logger.error("Groq fallback also failed: %s", exc)
                attempts.append(f"groq:{exc.__class__.__name__}")

        # ------------------------------------------------- nothing worked
        if trace:
            trace.add(
                "llm_generation",
                status="error",
                duration_ms=0,
                data={
                    "mode": "online",
                    "attempts": attempts,
                    "reason": "no online provider could complete the request",
                },
            )
        self._raise_online_failure(attempts, primary_error)
        raise AllProvidersFailed()  # unreachable, keeps type checkers happy

    def _raise_online_failure(self, attempts: list[str], primary_error: LLMError | None) -> None:
        """Turn a pile of provider errors into one useful message."""
        if not self.gemini.configured and not self.groq.configured:
            raise ProviderNotConfigured(
                "No online AI provider is configured on the server. Add GEMINI_API_KEY "
                "(and optionally GROQ_API_KEY as a fallback) to the .env file, or switch "
                "to Offline mode."
            )

        if isinstance(primary_error, RateLimitedError):
            raise ProviderRateLimited(
                "The AI providers are rate limiting us right now. Please wait a moment "
                "and try again, or switch to Offline mode."
            )

        detail = ", ".join(attempts) if attempts else "none configured"
        raise AllProvidersFailed(
            f"No AI provider could complete this request (tried: {detail}). "
            f"Check the provider status on the Settings page."
        )

    async def _with_retry(self, func, messages, temperature, max_tokens) -> LLMResponse:
        """One retry on a retryable failure (429, 5xx, timeout)."""
        try:
            return await func(messages, temperature=temperature, max_tokens=max_tokens)
        except LLMError as exc:
            if not exc.retryable:
                raise
            logger.info("Retrying after a retryable error: %s", exc)
            await asyncio.sleep(_RETRY_DELAY_SECONDS)
            return await func(messages, temperature=temperature, max_tokens=max_tokens)

    # =====================================================================
    # OFFLINE: local model only. No online fallback exists in this method.
    # =====================================================================
    async def _generate_offline(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None,
        max_tokens: int | None,
        trace: TraceRecorder | None,
    ) -> LLMResponse:
        if not self.local.model_present:
            if trace:
                trace.add(
                    "llm_generation",
                    status="error",
                    duration_ms=0,
                    data={
                        "mode": "offline",
                        "provider": "local",
                        "reason": "no local model file found",
                        "went_online": False,
                    },
                )
            raise LocalModelUnavailable(
                "Offline mode is selected, but no local model file was found at "
                "models/llm-model.gguf. Offline mode will not use an online provider. "
                "Restore the model file, or switch to Online mode."
            )

        load_started = asyncio.get_event_loop().time()
        loaded = await asyncio.to_thread(self.local.load)
        load_ms = int((asyncio.get_event_loop().time() - load_started) * 1000)

        if not loaded:
            if trace:
                trace.add(
                    "llm_generation",
                    status="error",
                    duration_ms=load_ms,
                    data={
                        "mode": "offline",
                        "provider": "local",
                        "reason": self.local._load_error or "model failed to load",
                        "went_online": False,
                    },
                )
            raise LocalModelUnavailable(
                "The local model could not be loaded. Offline mode will not fall back to "
                "an online provider, by design. Check the model file and the llama-cpp-python "
                "installation, or switch to Online mode."
            )

        try:
            response = await self.local.generate(
                messages, temperature=temperature, max_tokens=max_tokens
            )
        except LLMError as exc:
            if trace:
                trace.add(
                    "llm_generation",
                    status="error",
                    duration_ms=0,
                    data={
                        "mode": "offline",
                        "provider": "local",
                        "error": exc.__class__.__name__,
                        "message": str(exc),
                        "went_online": False,
                    },
                )
            raise

        if trace:
            trace.add(
                "llm_generation",
                duration_ms=response.latency_ms + load_ms,
                data={
                    "mode": "offline",
                    "provider": "local",
                    "model": response.model,
                    "role": "offline",
                    "used_fallback": False,
                    "went_online": False,
                    "model_load_ms": load_ms,
                    "generation_ms": response.latency_ms,
                    "token_usage": response.token_usage,
                    "finish_reason": response.finish_reason,
                    "note": "Generation ran entirely on this machine. No network request was made.",
                },
            )

        return response

    # =====================================================================
    # Introspection for the Settings page
    # =====================================================================
    def provider_statuses(self) -> list[ProviderStatus]:
        return [self.gemini.status(), self.groq.status(), self.local.status()]

    def mode_status(self) -> dict[str, Any]:
        """Which modes are usable right now, and why."""
        online_available = self.gemini.configured or self.groq.configured
        offline_available = self.local.status().available

        return {
            "online": {
                "available": online_available,
                "primary": "gemini",
                "fallback": "groq",
                "primary_configured": self.gemini.configured,
                "fallback_configured": self.groq.configured,
                "reason": (
                    ""
                    if online_available
                    else "No online provider is configured. Set GEMINI_API_KEY on the server."
                ),
            },
            "offline": {
                "available": offline_available,
                "provider": "local",
                "model": settings.local_model_label,
                "model_present": self.local.model_present,
                "reason": self.local.status().reason,
                "guarantee": (
                    "Offline mode never contacts an online provider. If the local model is "
                    "unavailable, you get a clear error or a labelled extractive answer - "
                    "never a silent switch to the internet."
                ),
            },
            "default_mode": settings.default_ai_mode,
        }

    async def list_provider_models(self) -> dict[str, Any]:
        """Live model lists, so configured IDs can be checked against reality."""
        gemini_models, groq_models = await asyncio.gather(
            self.gemini.list_models(),
            self.groq.list_models(),
            return_exceptions=False,
        )
        return {
            "gemini": {
                "configured": self.gemini.configured,
                "current": self.gemini.model,
                "models": gemini_models,
                "current_is_valid": (
                    self.gemini.model in gemini_models if gemini_models else None
                ),
            },
            "groq": {
                "configured": self.groq.configured,
                "current": self.groq.model,
                "models": groq_models,
                "current_is_valid": self.groq.model in groq_models if groq_models else None,
            },
            "local": self.local.info(),
        }


_adapter: LLMAdapter | None = None


def get_llm_adapter() -> LLMAdapter:
    global _adapter
    if _adapter is None:
        _adapter = LLMAdapter()
    return _adapter
