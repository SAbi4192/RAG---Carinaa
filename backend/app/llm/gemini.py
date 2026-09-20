"""
Gemini provider (primary, online).

API shape:  POST {base}/models/{model}:generateContent?key=API_KEY
Docs:       https://ai.google.dev/gemini-api/docs/text-generation

SECURITY
--------
The API key is read from server configuration and travels in the request URL to
Google. It never reaches the browser, never enters a prompt, and never appears in
a log line (see `app/core/logging.py`). The frontend only ever learns *whether* a
key is configured, never its value.
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
    flatten_messages,
    is_truncated,
)

logger = get_logger(__name__)

# Gemini's default safety threshold blocks a fair amount of legitimate academic
# material (security coursework, medical text, historical accounts). For a
# document-QA tool BLOCK_ONLY_HIGH is the right balance: it still filters clearly
# harmful content, but does not refuse to summarise a lecture on malware analysis.
_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
]


class GeminiProvider:
    name = "gemini"
    label = "Gemini"
    role = "primary"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = (api_key if api_key is not None else settings.gemini_api_key).strip()
        self.model = (model or settings.gemini_model).strip()
        self.base_url = settings.gemini_base_url.rstrip("/")
        self.timeout = settings.gemini_timeout_seconds

    # ------------------------------------------------------------------ status
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
            reason="" if self.configured else "GEMINI_API_KEY is not set on the server.",
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
            raise ProviderConfigError("Gemini is not configured (GEMINI_API_KEY missing).")

        system_text, user_text = flatten_messages(messages)

        # Gemini's reasoning tokens are charged AGAINST maxOutputTokens, so the
        # thinking allowance is added ON TOP of the caller's output budget. Without
        # this, a long chain of thought silently truncates the visible answer - see
        # the note on `gemini_thinking_budget` in config.py.
        output_budget = max_tokens or settings.gemini_max_output_tokens
        thinking_budget = settings.gemini_thinking_budget

        generation_config: dict[str, Any] = {
            "temperature": settings.gemini_temperature if temperature is None else temperature,
            "maxOutputTokens": output_budget + max(0, thinking_budget),
        }
        if thinking_budget >= 0:
            # An explicit 0 disables thinking outright; -1 leaves it to the model.
            generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": generation_config,
            "safetySettings": _SAFETY_SETTINGS,
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        url = f"{self.base_url}/models/{self.model}:generateContent"

        with Stopwatch() as watch:
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        url, params={"key": self.api_key}, json=payload
                    )
            except httpx.TimeoutException as exc:
                raise ProviderUnavailableError("Gemini timed out.") from exc
            except httpx.HTTPError as exc:
                raise ProviderUnavailableError(
                    f"Could not reach Gemini ({exc.__class__.__name__})."
                ) from exc

        if response.status_code == 429:
            raise RateLimitedError("Gemini is rate limiting us.")
        if response.status_code in (401, 403):
            raise ProviderConfigError(
                "Gemini rejected our credentials. Check that GEMINI_API_KEY is valid."
            )
        if response.status_code == 404:
            raise LLMError(
                f"Gemini does not recognise the model '{self.model}'. Pick a current model "
                f"on the Settings page."
            )
        if response.status_code >= 500:
            raise ProviderUnavailableError(f"Gemini returned HTTP {response.status_code}.")
        if response.status_code != 200:
            raise LLMError(f"Gemini returned HTTP {response.status_code}.")

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("Gemini returned a response we could not parse.") from exc

        text, finish_reason = self._extract_text(data)
        if not text.strip():
            # An empty candidate with a safety finish reason is a *blocked* answer,
            # not a network problem. Saying so helps the user understand why.
            if finish_reason and "SAFETY" in finish_reason.upper():
                raise LLMError(
                    "Gemini declined to answer this request due to its safety filters."
                )
            raise LLMError("Gemini returned an empty response.")

        usage = data.get("usageMetadata", {}) or {}

        # A MAX_TOKENS finish means the answer was CUT OFF, not finished. That must
        # never pass silently: a truncated answer can be missing a citation, a
        # number, or half its reasoning, while still reading as complete. This is
        # exactly how a Tamil translation came back with one citation instead of
        # two and four numbers missing.
        truncated = is_truncated(finish_reason)
        if truncated:
            logger.warning(
                "Gemini hit the output limit, so the answer was truncated "
                "(maxOutputTokens=%d, thoughts=%s, output=%s). Raise "
                "GEMINI_MAX_OUTPUT_TOKENS, or set GEMINI_THINKING_BUDGET=0 to stop "
                "reasoning tokens competing with the answer.",
                output_budget + max(0, thinking_budget),
                usage.get("thoughtsTokenCount"),
                usage.get("candidatesTokenCount"),
            )

        return LLMResponse(
            text=text.strip(),
            provider=self.name,
            model=self.model,
            latency_ms=watch.elapsed_ms,
            token_usage={
                "prompt": usage.get("promptTokenCount"),
                "completion": usage.get("candidatesTokenCount"),
                "total": usage.get("totalTokenCount"),
                # Surfaced so the RAG Trace shows where the budget actually went.
                "thoughts": usage.get("thoughtsTokenCount"),
                "truncated": truncated,
            },
            finish_reason=finish_reason,
        )

    def _extract_text(self, data: dict[str, Any]) -> tuple[str, str]:
        candidates = data.get("candidates") or []
        if not candidates:
            prompt_feedback = data.get("promptFeedback") or {}
            blocked = prompt_feedback.get("blockReason")
            if blocked:
                return "", f"SAFETY:{blocked}"
            return "", ""

        candidate = candidates[0]
        finish_reason = str(candidate.get("finishReason", ""))

        parts = (candidate.get("content") or {}).get("parts") or []
        chunks: list[str] = []
        for part in parts:
            text = part.get("text")
            if text:
                chunks.append(str(text))

        return "".join(chunks), finish_reason

    # --------------------------------------------------------------- model list
    async def list_models(self) -> list[str]:
        """Live model list, filtered to those that support generateContent.

        This is why no model ID in this project can go stale: the Settings page asks
        the provider what it currently offers and lets the operator choose.
        """
        if not self.configured:
            return []

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    f"{self.base_url}/models", params={"key": self.api_key}
                )
            if response.status_code != 200:
                return []
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return []

        models: list[str] = []
        for entry in data.get("models", []):
            methods = entry.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            name = str(entry.get("name", ""))
            models.append(name.split("/", 1)[-1] if "/" in name else name)

        return sorted(models)
