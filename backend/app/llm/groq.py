"""
Groq provider (primary, online).

API shape:  POST {base}/chat/completions   (OpenAI-compatible)
Docs:       https://console.groq.com/docs/models

WHY GROQ IS PRIMARY
-------------------
It is OpenAI-API-compatible, extremely fast (LPU inference), and its production
model line is stable - which makes it the right provider for live demos, where
latency IS the user experience. When Groq is rate-limited or down, the adapter
falls back to Gemini, and the UI says so, explicitly:

    "Gemini · Fallback"

We never present a fallback answer as if the primary provider produced it. A
silently hidden fallback would mean the user cannot reason about which model wrote
what, which matters when you are citing documents.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.base import (
    LLMError,
    LLMResponse,
    ProviderConfigError,
    ProviderStatus,
    ProviderUnavailableError,
    RateLimitedError,
    Stopwatch,
    is_truncated,
    iter_sse_data,
)

logger = get_logger(__name__)


class GroqProvider:
    name = "groq"
    label = "Groq"
    role = "primary"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = (api_key if api_key is not None else settings.groq_api_key).strip()
        self.model = (model or settings.groq_model).strip()
        self.base_url = settings.groq_base_url.rstrip("/")
        self.timeout = settings.groq_timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            label=self.label,
            role=self.role,
            configured=self.configured,
            available=self.configured,
            model=self.model,
            reason="" if self.configured else "GROQ_API_KEY is not set on the server.",
            modes=("online",),
        )

    # -------------------------------------------------------------- generation
    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if not self.configured:
            raise ProviderConfigError("Groq is not configured (GROQ_API_KEY missing).")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.groq_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.groq_max_output_tokens,
        }

        with Stopwatch() as watch:
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
            except httpx.TimeoutException as exc:
                raise ProviderUnavailableError("Groq timed out.") from exc
            except httpx.HTTPError as exc:
                raise ProviderUnavailableError(
                    f"Could not reach Groq ({exc.__class__.__name__})."
                ) from exc

        if response.status_code == 429:
            raise RateLimitedError("Groq is rate limiting us.")
        if response.status_code in (401, 403):
            raise ProviderConfigError(
                "Groq rejected our credentials. Check that GROQ_API_KEY is valid."
            )
        if response.status_code == 404:
            # RETRYABLE, same reasoning as Gemini: one unknown model says nothing about the
            # rest of the chain. See the note in gemini.py.
            raise LLMError(
                f"Groq does not recognise the model '{self.model}'. Pick a current model "
                f"on the Settings page.",
                retryable=True,
                status_code=404,
            )
        if response.status_code >= 500:
            raise ProviderUnavailableError(f"Groq returned HTTP {response.status_code}.")
        if response.status_code != 200:
            raise LLMError(f"Groq returned HTTP {response.status_code}.")

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("Groq returned a response we could not parse.") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMError("Groq returned no completion choices.")

        choice = choices[0]
        text = str((choice.get("message") or {}).get("content") or "").strip()
        if not text:
            raise LLMError("Groq returned an empty response.")

        usage = data.get("usage", {}) or {}
        finish_reason = str(choice.get("finish_reason", "") or "")

        # OpenAI-compatible providers say "length" when they ran out of output
        # budget. Without this the truncation was invisible: a Tamil translation
        # stopped mid-sentence, still passed validation, and reached the user with
        # no warning at all.
        truncated = is_truncated(finish_reason)
        if truncated:
            logger.warning(
                "Groq hit the output limit, so the answer was truncated "
                "(max_tokens=%s, completion=%s).",
                max_tokens,
                usage.get("completion_tokens"),
            )

        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=watch.elapsed_ms,
            token_usage={
                "prompt": usage.get("prompt_tokens"),
                "completion": usage.get("completion_tokens"),
                "total": usage.get("total_tokens"),
                "truncated": truncated,
            },
            finish_reason=finish_reason,
        )

    # --------------------------------------------------------------- streaming
    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Yield Groq's real token deltas, OpenAI-style.

        Raises the SAME errors as `generate` so the adapter's fallback rules apply
        identically to a streaming request. That matters: a rate-limited stream must
        advance the chain exactly as a rate-limited completion does.

        A stream that ends with zero fragments raises the same "empty response"
        `LLMError` the buffered path raises. Without it, an empty provider response
        would stream nothing, the pipeline would join an empty list, and the route
        would commit an empty assistant message as if it were an answer.

        `meta`, when provided, receives `finish_reason` so callers can tell
        `length` truncation apart from a clean stop without parsing the stream
        twice.
        """
        if not self.configured:
            raise ProviderConfigError("Groq is not configured (GROQ_API_KEY missing).")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.groq_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.groq_max_output_tokens,
            "stream": True,
        }

        fragments = 0
        finish_reason = ""

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as response:
                    if response.status_code == 429:
                        raise RateLimitedError("Groq is rate limiting us.")
                    if response.status_code in (401, 403):
                        raise ProviderConfigError(
                            "Groq rejected our credentials. Check that GROQ_API_KEY is valid."
                        )
                    if response.status_code == 404:
                        raise LLMError(
                            f"Groq does not recognise the model '{self.model}'. Pick a current "
                            f"model on the Settings page.",
                            retryable=True,
                            status_code=404,
                        )
                    if response.status_code >= 500:
                        raise ProviderUnavailableError(
                            f"Groq returned HTTP {response.status_code}."
                        )
                    if response.status_code != 200:
                        raise LLMError(f"Groq returned HTTP {response.status_code}.")

                    async for data in iter_sse_data(response.aiter_lines()):
                        try:
                            event = json.loads(data)
                        except ValueError:
                            continue
                        for choice in event.get("choices") or []:
                            delta = (choice.get("delta") or {}).get("content")
                            if delta:
                                fragments += 1
                                yield str(delta)
                            reason = choice.get("finish_reason")
                            if reason:
                                finish_reason = str(reason)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError("Groq timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Could not reach Groq ({exc.__class__.__name__})."
            ) from exc

        if meta is not None and finish_reason:
            meta["finish_reason"] = finish_reason
        if fragments == 0:
            raise LLMError("Groq returned an empty response.")

    # --------------------------------------------------------------- model list
    async def list_models(self) -> list[str]:
        """Live model list from GroqCloud."""
        if not self.configured:
            return []

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            if response.status_code != 200:
                return []
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return []

        models: list[str] = []
        for entry in data.get("data", []):
            model_id = entry.get("id")
            if model_id:
                models.append(str(model_id))

        return sorted(models)
