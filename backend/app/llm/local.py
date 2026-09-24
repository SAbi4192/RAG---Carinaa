"""
Local LLM provider (offline mode) - llama.cpp over the bundled GGUF.

THE MODEL
---------
    models/llm-model.gguf
    Qwen2.5-3B-Instruct, Q4_K_M, 36 layers, 32768-token context,
    chat template embedded in the GGUF metadata.

Verified by reading the GGUF header directly (see `scripts/inspect_gguf.py`),
not assumed from the filename.

WHY llama.cpp / GGUF
--------------------
  * Runs on CPU. No GPU, no CUDA, no cloud account.
  * The weights are a single file on disk. There is no download at query time.
  * 3B parameters at Q4_K_M is roughly 2 GB in RAM - it fits on a student laptop.

THE OFFLINE GUARANTEE (spec section 15)
---------------------------------------
> Offline mode must never silently switch to an online provider.

This module makes no network calls of any kind. llama.cpp reads the GGUF from
local disk and computes on the local CPU. There is no HTTP client here, no
telemetry, and no model download at inference time.

If the model cannot be loaded, we do NOT fall back to Gemini or Groq. The adapter
either reports a clear error or produces a clearly-labelled extractive answer built
only from retrieved evidence (see `app/rag/failsafe.py`). Either way the offline
guarantee holds, and `tests/test_offline_network.py` proves it by failing the test
if any outbound socket is opened.

WHY A LOCK
----------
A llama.cpp context holds mutable state (the KV cache). Two threads generating at
once would interleave their state and produce garbage. One lock, one generation at
a time. That is also the honest thing to do: this is a laptop-class model, not a
throughput server.
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.base import (
    LLMError,
    LLMResponse,
    ProviderStatus,
    Stopwatch,
    is_truncated,
)

logger = get_logger(__name__)


def _default_threads() -> int:
    """Physical cores are a better default than logical cores for llama.cpp."""
    try:
        physical = os.cpu_count() or 4
        return max(1, min(physical, 8))
    except Exception:  # pragma: no cover
        return 4


class LocalProvider:
    """Offline generation via llama.cpp. No network, ever."""

    name = "local"
    label = "Local model"
    role = "offline"

    def __init__(
        self,
        model_path: Path | str | None = None,
        *,
        n_ctx: int | None = None,
        n_threads: int | None = None,
        n_gpu_layers: int | None = None,
    ) -> None:
        self.model_path = Path(model_path or settings.local_model_path)
        self.n_ctx = n_ctx or settings.local_n_ctx
        self.n_threads = n_threads or settings.local_n_threads or _default_threads()
        self.n_gpu_layers = (
            n_gpu_layers if n_gpu_layers is not None else settings.local_n_gpu_layers
        )

        self._llm: Any = None
        self._lock = threading.RLock()
        self._load_lock = threading.Lock()
        self._load_error: str | None = None
        self._loaded_at: float | None = None
        self._load_seconds: float | None = None

    # ------------------------------------------------------------------ status
    @property
    def model_present(self) -> bool:
        try:
            return self.model_path.is_file() and self.model_path.stat().st_size > 0
        except OSError:
            return False

    @property
    def is_loaded(self) -> bool:
        return self._llm is not None

    def status(self) -> ProviderStatus:
        present = self.model_present
        runtime_ok, runtime_reason = self._runtime_available()
        available = present and runtime_ok
        reason = ""
        if not present:
            reason = f"No model file found at {self.model_path.name}."
        elif not runtime_ok:
            reason = runtime_reason

        return ProviderStatus(
            name=self.name,
            label=self.label,
            role=self.role,
            configured=present,
            available=available,
            model=settings.local_model_label,
            reason=reason,
            modes=("offline",),
        )

    def _runtime_available(self) -> tuple[bool, str]:
        try:
            import llama_cpp  # noqa: F401
        except ImportError:
            return False, (
                "llama-cpp-python is not installed. Install it from the CPU wheel "
                "index (see backend/requirements.txt)."
            )
        return True, ""

    # -------------------------------------------------------------------- load
    def load(self) -> bool:
        """Load the GGUF into memory. Idempotent and thread-safe."""
        if self._llm is not None:
            return True

        with self._load_lock:
            if self._llm is not None:
                return True

            if not self.model_present:
                self._load_error = f"Model file not found: {self.model_path}"
                logger.error(self._load_error)
                return False

            runtime_ok, reason = self._runtime_available()
            if not runtime_ok:
                self._load_error = reason
                return False

            try:
                from llama_cpp import Llama

                size_gb = self.model_path.stat().st_size / (1024**3)
                logger.info(
                    "Loading local model %s (%.2f GB) with n_ctx=%d threads=%d gpu_layers=%d...",
                    self.model_path.name,
                    size_gb,
                    self.n_ctx,
                    self.n_threads,
                    self.n_gpu_layers,
                )
                started = time.perf_counter()

                self._llm = Llama(
                    model_path=str(self.model_path),
                    n_ctx=self.n_ctx,
                    n_threads=self.n_threads,
                    n_gpu_layers=self.n_gpu_layers,
                    # Chat template comes from the GGUF metadata (Qwen2.5 ships one),
                    # so we do not hand-roll a prompt format here.
                    verbose=settings.local_verbose,
                    logits_all=False,
                    embedding=False,
                )

                self._load_seconds = round(time.perf_counter() - started, 2)
                self._loaded_at = time.time()
                self._load_error = None
                logger.info("Local model ready in %.1fs", self._load_seconds)
                return True

            except Exception as exc:
                self._load_error = f"{exc.__class__.__name__}: {exc}"
                logger.error("Could not load local model: %s", self._load_error)
                self._llm = None
                return False

    def unload(self) -> None:
        """Free the model from memory (Settings page action)."""
        with self._lock:
            self._llm = None
            self._loaded_at = None
            logger.info("Local model unloaded.")

    # -------------------------------------------------------------- generation
    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate a completion on the local CPU.

        Runs in a worker thread because llama.cpp is blocking C code; blocking the
        asyncio event loop would freeze every other request in the process.
        """
        if not self.load():
            raise LLMError(
                "The local model is not available, so Offline mode cannot generate an "
                "answer. Check that models/llm-model.gguf exists and that "
                "llama-cpp-python is installed.",
                retryable=False,
            )

        temperature = settings.local_temperature if temperature is None else temperature
        max_tokens = max_tokens or settings.local_max_tokens

        # Rough guard: refuse rather than silently truncate the prompt, because a
        # silently truncated context produces a confidently wrong answer.
        approx_prompt_tokens = sum(len(m.get("content", "")) for m in messages) // 4
        if approx_prompt_tokens > self.n_ctx - max_tokens - 64:
            raise LLMError(
                f"The retrieved context is too large for the local model's {self.n_ctx}-token "
                f"window (~{approx_prompt_tokens} tokens of prompt). Reduce Top-K or the "
                f"context budget, or raise LOCAL_N_CTX.",
                retryable=False,
            )

        with Stopwatch() as watch:
            try:
                result = await asyncio.to_thread(
                    self._complete, messages, temperature, max_tokens
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Local generation failed")
                raise LLMError(
                    "The local model failed while generating an answer.",
                    retryable=False,
                ) from exc

        text = str(result.get("text", "")).strip()
        if not text:
            raise LLMError("The local model returned an empty response.")

        usage = result.get("usage", {}) or {}
        finish_reason = str(result.get("finish_reason", "") or "")

        # llama.cpp reports "length" when the model ran out of output budget. A
        # cut-off offline answer is just as misleading as a cut-off online one, so
        # it is reported the same way.
        truncated = is_truncated(finish_reason)
        if truncated:
            logger.warning(
                "The local model hit the output limit, so the answer was truncated "
                "(completion=%s).",
                usage.get("completion_tokens"),
            )

        return LLMResponse(
            text=text,
            provider=self.name,
            model=settings.local_model_label,
            latency_ms=watch.elapsed_ms,
            token_usage={
                "prompt": usage.get("prompt_tokens"),
                "completion": usage.get("completion_tokens"),
                "total": usage.get("total_tokens"),
                "truncated": truncated,
            },
            finish_reason=finish_reason,
        )

    def _complete(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> dict[str, Any]:
        """Blocking completion. Serialised by the lock."""
        with self._lock:
            response = self._llm.create_chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=settings.local_top_p,
                repeat_penalty=settings.local_repeat_penalty,
            )

        choices = response.get("choices") or [{}]
        choice = choices[0]
        return {
            "text": (choice.get("message") or {}).get("content", ""),
            "finish_reason": choice.get("finish_reason", ""),
            "usage": response.get("usage", {}),
        }

    async def list_models(self) -> list[str]:
        """The local model is whatever file is on disk. There is no registry."""
        return [settings.local_model_label] if self.model_present else []

    # --------------------------------------------------------------- streaming
    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Yield real token deltas from llama.cpp, order-preserving.

        llama.cpp's iterator is a BLOCKING Python generator, so it runs in a worker
        thread and hands fragments to the event loop through a queue. Two guarantees
        hold: the lock is held for the whole generation (a llama.cpp context is
        single-state, so two concurrent streams would corrupt the KV cache), and no
        network call is made - the offline guarantee applies to streaming too.

        A stream that yields nothing raises the same "empty response" error the
        buffered path raises, so an empty local generation can never be committed
        as a complete answer. The finish reason is reported through `meta`.
        """
        if not self.load():
            raise LLMError(
                "The local model is not available, so Offline mode cannot generate an "
                "answer. Check that models/llm-model.gguf exists and that "
                "llama-cpp-python is installed.",
                retryable=False,
            )

        temperature = settings.local_temperature if temperature is None else temperature
        max_tokens = max_tokens or settings.local_max_tokens

        approx_prompt_tokens = sum(len(m.get("content", "")) for m in messages) // 4
        if approx_prompt_tokens > self.n_ctx - max_tokens - 64:
            raise LLMError(
                f"The retrieved context is too large for the local model's {self.n_ctx}-token "
                f"window (~{approx_prompt_tokens} tokens of prompt). Reduce Top-K or the "
                f"context budget, or raise LOCAL_N_CTX.",
                retryable=False,
            )

        fragments: queue.Queue[Any] = queue.Queue()
        sentinel = object()
        shared: dict[str, Any] = {"fragments": 0, "finish_reason": ""}

        def produce() -> None:
            try:
                with self._lock:
                    for chunk in self._llm.create_chat_completion(
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        top_p=settings.local_top_p,
                        repeat_penalty=settings.local_repeat_penalty,
                        stream=True,
                    ):
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = (choices[0].get("delta") or {}).get("content")
                        if delta:
                            shared["fragments"] += 1
                            fragments.put(str(delta))
                        reason = (choices[0] or {}).get("finish_reason")
                        if reason:
                            shared["finish_reason"] = str(reason)
                fragments.put(sentinel)
            except Exception as exc:  # noqa: BLE001 - re-raised in the consumer
                fragments.put(exc)

        thread = threading.Thread(target=produce, name="carinaa-local-stream", daemon=True)
        thread.start()

        while True:
            item = await asyncio.to_thread(fragments.get)
            if item is sentinel:
                break
            if isinstance(item, Exception):
                logger.exception("Local streaming failed", exc_info=item)
                raise LLMError(
                    "The local model failed while generating an answer.",
                    retryable=False,
                ) from item
            yield item

        if meta is not None and shared.get("finish_reason"):
            meta["finish_reason"] = shared["finish_reason"]
        if shared["fragments"] == 0:
            raise LLMError("The local model returned an empty response.")

    # ------------------------------------------------------------------- info
    def info(self) -> dict[str, Any]:
        size_gb = 0.0
        try:
            size_gb = round(self.model_path.stat().st_size / (1024**3), 2)
        except OSError:
            pass

        return {
            "name": self.name,
            "label": settings.local_model_label,
            "model_path": str(self.model_path),
            "model_present": self.model_present,
            "size_gb": size_gb,
            "loaded": self.is_loaded,
            "load_seconds": self._load_seconds,
            "load_error": self._load_error,
            "n_ctx": self.n_ctx,
            "n_threads": self.n_threads,
            "n_gpu_layers": self.n_gpu_layers,
            "max_tokens": settings.local_max_tokens,
            "offline": True,
            "makes_network_calls": False,
            "architecture": "qwen2",
            "quantization": "Q4_K_M",
            "parameters": "3.4B",
        }


_provider: LocalProvider | None = None
_provider_lock = threading.Lock()


def get_local_provider() -> LocalProvider:
    global _provider
    if _provider is None:
        with _provider_lock:
            if _provider is None:
                _provider = LocalProvider()
    return _provider
