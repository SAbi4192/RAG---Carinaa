"""
The LLM adapter - the only place provider routing is decided.

    RAG pipeline  ->  LLM adapter  ->  provider

ONLINE MODE
-----------
    RAG context -> LLM adapter -> Groq
                                    | failure (rate limit, outage, bad key)
                                    v
                                  Gemini

Groq is primary, Gemini is fallback, and the fallback is ALWAYS disclosed. The
`LLMResponse` carries `used_fallback` and `fallback_reason`, which the UI renders
as "Gemini · Fallback" instead of quietly showing "Gemini".

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
from typing import Any, AsyncIterator

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
    # ONLINE: Groq -> Gemini
    # =====================================================================
    # =====================================================================
    # Model chaining
    # =====================================================================

    @staticmethod
    def _model_chain(raw: str, fallback: str) -> list[str]:
        """Parse a comma-separated model list, deduplicated, configured model first.

        The configured model is moved to the front rather than merely inserted if missing.
        That distinction matters: the configured (settings.gemini_model / groq_model) is the
        operator's explicit choice, so it must be attempted FIRST even when it already
        appears later in the chain string. The previous `insert(0, ...)` only fired when the
        model was absent from the raw list, meaning the operator's choice was silently tried
        second, and the first-listed model was tried first. If the configured model is the
        one that works, we wasted a request on a model we did not choose.

        Ordering: configured -> remaining chain entries -> nothing else.
        """
        models: list[str] = []
        for item in (raw or "").split(","):
            name = item.strip()
            if name and name not in models:
                models.append(name)

        if not models:
            return [fallback] if fallback else []

        if fallback:
            # Move to front, removing any later occurrence — order, not just presence.
            models = [fallback] + [m for m in models if m != fallback]

        return models

    async def _generate_with_model_chain(
        self,
        provider_cls: Any,
        raw_models: str,
        fallback_model: str,
        messages: list[dict[str, str]],
        temperature: float | None,
        max_tokens: float | None,
        *,
        provider_name: str,
    ) -> tuple[LLMResponse, list[str]]:
        """Try the provider's models in order; return the first that answers.

        Returns (response, skipped) where `skipped` lists the models that failed, so
        the caller can log and trace them honestly.
        """
        models = self._model_chain(raw_models, fallback_model)
        last_error: LLMError | None = None
        skipped: list[str] = []

        for position, model in enumerate(models):
            provider = provider_cls(model=model)
            if not provider.configured:
                break

            try:
                if position == 0:
                    # The preferred model keeps the existing single retry.
                    response = await self._with_retry(
                        provider.generate, messages, temperature, max_tokens
                    )
                else:
                    response = await provider.generate(
                        messages, temperature=temperature, max_tokens=max_tokens
                    )
                if skipped:
                    logger.warning(
                        "%s: skipped %s; answered on %s",
                        provider_name,
                        ", ".join(skipped),
                        model,
                    )
                return response, skipped
            except LLMError as exc:
                skipped.append(f"{model}({exc.__class__.__name__})")
                last_error = exc
                if not exc.retryable:
                    # A malformed request fails identically everywhere - advancing
                    # would only burn time and bury the real cause.
                    break

        if last_error is not None:
            # Which models were tried is the single most useful thing to know when a
            # provider is rate limiting: it distinguishes "one model is busy" from
            # "the whole account is out of quota".
            setattr(last_error, "tried_models", list(skipped))
        raise last_error or LLMError(f"No {provider_name} model could complete the request.")

    @staticmethod
    def _tried_models(exc: LLMError) -> list[str]:
        """Models the chain attempted before failing."""
        return list(getattr(exc, "tried_models", None) or [])

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

        # ---------------------------------------------------------- Groq (primary)
        if self.groq.configured:
            attempts.append("groq")
            try:
                response, skipped_models = await self._generate_with_model_chain(
                    GroqProvider,
                    settings.groq_models,
                    settings.groq_model,
                    messages,
                    temperature,
                    max_tokens,
                    provider_name="Groq",
                )
                if trace:
                    trace.add(
                        "llm_generation",
                        duration_ms=response.latency_ms,
                        data={
                            "mode": "online",
                            "provider": "groq",
                            "model": response.model,
                            "role": "primary",
                            "used_fallback": False,
                            "token_usage": response.token_usage,
                            "finish_reason": response.finish_reason,
                            # Present only when an earlier model was unavailable.
                            **(
                                {"skipped_models": skipped_models} if skipped_models else {}
                            ),
                        },
                    )
                return response
            except LLMError as exc:
                primary_error = exc
                logger.warning("Groq failed, trying Gemini: %s", exc)
                if trace:
                    trace.add(
                        "llm_generation",
                        status="error",
                        duration_ms=0,
                        data={
                            "mode": "online",
                            "provider": "groq",
                            "role": "primary",
                            "error": exc.__class__.__name__,
                            "message": str(exc),
                            "action": "falling back to Gemini",
                            **(
                                {"models_tried": self._tried_models(exc)}
                                if self._tried_models(exc)
                                else {}
                            ),
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
                        "provider": "groq",
                        "role": "primary",
                        "reason": "GROQ_API_KEY is not configured on the server",
                    },
                )

        # ---------------------------------------------------------- Gemini (fallback)
        if self.gemini.configured:
            attempts.append("gemini")
            try:
                response, skipped_models = await self._generate_with_model_chain(
                    GeminiProvider,
                    settings.gemini_models,
                    settings.gemini_model,
                    messages,
                    temperature,
                    max_tokens,
                    provider_name="Gemini",
                )
                # Disclose the fallback. Never hide it.
                response.used_fallback = True
                response.primary_attempted = "groq" if primary_error else "none"
                response.fallback_reason = (
                    str(primary_error) if primary_error else "Groq was not configured."
                )
                if trace:
                    trace.add(
                        "llm_generation",
                        duration_ms=response.latency_ms,
                        data={
                            "mode": "online",
                            "provider": "gemini",
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
                logger.error("Gemini fallback also failed: %s", exc)
                attempts.append(f"gemini:{exc.__class__.__name__}")

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
        if not self.groq.configured and not self.gemini.configured:
            raise ProviderNotConfigured(
                "No online AI provider is configured on the server. Add GROQ_API_KEY "
                "(and optionally GEMINI_API_KEY as a fallback) to the .env file, or switch "
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
    # Streaming
    # =====================================================================
    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        mode: str = "online",
        temperature: float | None = None,
        max_tokens: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Stream a completion, honouring the selected AI mode exactly.

        The provider order and fallback rules are the SAME as `generate`:
        online goes Groq (primary) then Gemini (fallback) across the model chain,
        and offline streams only from the local model with no online fallback.

        The crucial difference is WHEN the fallback decision is made. A streaming
        response commits to a provider the moment it yields its first token - a
        browser cannot un-see text. So a provider failure is only recoverable while
        NOTHING has been emitted yet; once bytes are on the wire the stream is
        committed, because silently continuing with a different model would present
        a hybrid answer as if one model produced it. That is the same honesty rule
        that governs non-streaming fallback, applied to a stricter timeline.

        `meta`, when supplied, is filled in with the provider and model that
        actually produced the stream, so the caller can record real provenance on
        the trace instead of guessing it.
        """
        normalized_mode = (mode or "online").strip().lower()
        if normalized_mode not in ("online", "offline"):
            raise ValueError(f"Unknown AI mode '{mode}'.")

        if normalized_mode == "offline":
            async for fragment in self._stream_offline(
                messages, temperature=temperature, max_tokens=max_tokens, meta=meta
            ):
                yield fragment
            return

        async for fragment in self._stream_online(
            messages, temperature=temperature, max_tokens=max_tokens, meta=meta
        ):
            yield fragment

    async def _stream_online(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None,
        max_tokens: int | None,
        meta: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Stream from Groq, falling through to Gemini only before the first token."""
        primary_error: LLMError | None = None
        yielded = False

        if self.groq.configured:
            chain = self._model_chain(settings.groq_models, settings.groq_model)
            for model in chain:
                provider = GroqProvider(model=model)
                if not provider.configured:
                    break
                try:
                    async for fragment in provider.stream(
                        messages, temperature=temperature, max_tokens=max_tokens
                    ):
                        if meta is not None and not meta:
                            meta.update(
                                {
                                    "provider": "groq",
                                    "model": model,
                                    "role": "primary",
                                    "used_fallback": False,
                                }
                            )
                        yielded = True
                        yield fragment
                    return
                except LLMError as exc:
                    primary_error = exc
                    if yielded:
                        # Text already reached the client. Committing here is the only
                        # honest option: re-streaming from another model would deliver an
                        # answer that is part Groq, part Gemini, under one provider label.
                        raise
                    if not exc.retryable:
                        break
            if yielded:
                return

        if self.gemini.configured:
            chain = self._model_chain(settings.gemini_models, settings.gemini_model)
            for model in chain:
                provider = GeminiProvider(model=model)
                if not provider.configured:
                    break
                try:
                    async for fragment in provider.stream(
                        messages, temperature=temperature, max_tokens=max_tokens
                    ):
                        if meta is not None and not meta:
                            meta.update(
                                {
                                    "provider": "gemini",
                                    "model": model,
                                    "role": "fallback",
                                    "used_fallback": True,
                                    "fallback_reason": (
                                        str(primary_error)
                                        if primary_error
                                        else "Groq was not configured."
                                    ),
                                }
                            )
                        yielded = True
                        yield fragment
                    return
                except LLMError as exc:
                    primary_error = exc
                    if yielded:
                        raise
                    if not exc.retryable:
                        break

        if not yielded:
            attempts: list[str] = []
            if self.groq.configured:
                attempts.append("groq")
            if self.gemini.configured:
                attempts.append("gemini")
            self._raise_online_failure(attempts, primary_error)
            raise AllProvidersFailed()  # unreachable

    async def _stream_offline(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None,
        max_tokens: int | None,
        meta: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Stream from the local model only. There is no online fallback in this method."""
        if not self.local.model_present:
            raise LocalModelUnavailable(
                "Offline mode is selected, but no local model file was found at "
                "models/llm-model.gguf. Offline mode will not use an online provider. "
                "Restore the model file, or switch to Online mode."
            )
        loaded = await asyncio.to_thread(self.local.load)
        if not loaded:
            raise LocalModelUnavailable(
                "The local model could not be loaded. Offline mode will not fall back to "
                "an online provider, by design. Check the model file and the llama-cpp-python "
                "installation, or switch to Online mode."
            )
        if meta is not None:
            meta.update(
                {
                    "provider": "local",
                    "model": settings.local_model_label,
                    "role": "offline",
                    "used_fallback": False,
                }
            )
        async for fragment in self.local.stream(
            messages, temperature=temperature, max_tokens=max_tokens
        ):
            yield fragment

    # =====================================================================
    # Introspection for the Settings page
    # =====================================================================
    def provider_statuses(self) -> list[ProviderStatus]:
        return [self.groq.status(), self.gemini.status(), self.local.status()]

    def mode_status(self) -> dict[str, Any]:
        """Which modes are usable right now, and why."""
        online_available = self.groq.configured or self.gemini.configured
        offline_available = self.local.status().available

        return {
            "online": {
                "available": online_available,
                "primary": "groq",
                "fallback": "gemini",
                "primary_configured": self.groq.configured,
                "fallback_configured": self.gemini.configured,
                "reason": (
                    ""
                    if online_available
                    else "No online provider is configured. Set GROQ_API_KEY on the server."
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
