"""
Groq provider (fallback, online).

API shape:  POST {base}/chat/completions   (OpenAI-compatible)
Docs:       https://console.groq.com/docs/models

WHY GROQ IS THE FALLBACK
------------------------
It is OpenAI-API-compatible, extremely fast (LPU inference), and its production
model line is stable. When Gemini is rate-limited or down, the user still gets an
answer - and the UI says so, explicitly:

    "Groq · Fallback"

We never present a fallback answer as if the primary provider produced it. A
silently hidden fallback would mean the user cannot reason about which model wrote
what, which matters when you are citing documents.
"""

from __future__ import annotations

from typing import Any

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
)

logger = get_logger(__name__)


class GroqProvider:
    name = "groq"
    label = "Groq"
    role = "fallback"

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
            raise LLMError(
                f"Groq does not recognise the model '{self.model}'. Pick a current model "
                f"on the Settings page."
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
