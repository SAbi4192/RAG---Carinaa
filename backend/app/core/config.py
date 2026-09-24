"""
Carinaa configuration.

WHY THIS FILE EXISTS
--------------------
Every tunable value in the RAG pipeline lives here and nowhere else. The rule is:
*configuration is separate from business logic*.

If you want to change the chunk size, the embedding model, the retrieval depth,
or the provider model IDs, you change it in `.env` (or here as a default) — you
do NOT go hunting through the retrieval code. This matters for the college demo
because you can show the instructor "here is the knob for Top-K" and then turn
the knob live.

SECRETS
-------
`GEMINI_API_KEY` and `GROQ_API_KEY` are read from the environment on the SERVER
only. They are never sent to the browser, never embedded in the frontend build,
and never placed into an LLM prompt. See `app/llm/` and `app/core/logging.py`.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# --------------------------------------------------------------------------
# Path resolution
# --------------------------------------------------------------------------
# backend/app/core/config.py  ->  backend/app/core  ->  backend/app
# ->  backend  ->  <project root>
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """All Carinaa runtime settings.

    Environment variables are matched case-insensitively, so `gemini_api_key`
    can be set as `GEMINI_API_KEY=...` in `.env`.
    """

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- app ---
    app_name: str = "Carinaa"
    app_tagline: str = "Every Answer, Traceable."
    app_version: str = "1.0.0"
    environment: Literal["development", "production", "test"] = "development"
    debug: bool = True

    host: str = "127.0.0.1"
    port: int = 8000

    # Comma-separated list, or "*" for all (development only).
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ------------------------------------------------------------- security ---
    # MUST be overridden in production. A random value is generated per-process
    # if unset, which invalidates existing tokens on restart (safe default).
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    bcrypt_rounds: int = 12

    # ------------------------------------------------------------- storage ---
    data_dir: Path = PROJECT_ROOT / "data"
    upload_dir: Path = PROJECT_ROOT / "data" / "uploads"
    chroma_dir: Path = PROJECT_ROOT / "data" / "chroma"
    database_url: str = ""  # derived below if empty
    max_upload_mb: int = 50

    # ---------------------------------------------------------- ingestion ---
    chunk_size: int = 1000          # characters per chunk (target)
    chunk_overlap: int = 150        # characters of overlap between chunks
    min_chunk_chars: int = 80       # drop fragments smaller than this
    max_chunk_chars: int = 4000     # hard ceiling; force-split beyond this

    # ---------------------------------------------------------- embeddings ---
    # Local ONNX model, cached on disk after first download.
    #
    # CHOSEN FOR: multilingual coverage (50+ languages incl. Tamil, Malayalam,
    # Telugu, Hindi, Japanese), small download (0.22 GB), and fast CPU inference -
    # it is 384-dimensional, so indexing a 200-page PDF takes seconds, not minutes.
    #
    # ALTERNATIVES (change this one value, then re-index from the UI):
    #   intfloat/multilingual-e5-large                  dim 1024, 2.24 GB  - best quality
    #   sentence-transformers/paraphrase-multilingual-mpnet-base-v2  dim 768, 1.0 GB
    #   BAAI/bge-small-en-v1.5                          dim  384, 0.07 GB  - English only
    #
    # Verified present in `fastembed.TextEmbedding.list_supported_models()`.
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    # Some model families (e5, bge) are trained with asymmetric prefixes such as
    # "query: " / "passage: ". `fastembed` applies the correct prefix for each
    # model it knows about, so these stay empty. Setting them here would apply the
    # prefix TWICE and measurably hurt retrieval. They exist only as an escape
    # hatch for a model fastembed does not know about.
    embedding_query_prefix: str = ""
    embedding_passage_prefix: str = ""
    embedding_batch_size: int = 32
    embedding_cache_dir: Path = PROJECT_ROOT / "data" / "models" / "embeddings"
    # Optional: a directory holding the ONNX model files directly
    # (model_optimized.onnx, config.json, tokenizer.json, ...).
    #
    # WHY THIS EXISTS: `fastembed` normally pulls the model through the HuggingFace
    # hub client. Some locked-down or proxied environments produce truncated files
    # from that client. When this directory contains a complete model, we hand it to
    # fastembed via `specific_model_path`, which skips the hub client entirely.
    #
    # Run `python scripts/fetch_models.py` to populate it with plain HTTPS
    # downloads. If the directory is empty, we fall back to the normal cache path.
    embedding_local_dir: Path = PROJECT_ROOT / "data" / "models" / "fastembed"
    embedding_onnx_filename: str = "model_optimized.onnx"

    # ----------------------------------------------------------- retrieval ---
    top_k: int = 5                  # chunks handed to the LLM
    # Chunks fetched before re-ranking. This is RECALL: with a large workspace the
    # candidate pool is a small sample of the store (20 of 536 chunks is under 4%),
    # so a relevant passage near the edge of the pool can be missed entirely, and no
    # amount of re-ranking will bring it back. Raised from 20 to 30 because it costs
    # almost nothing - vector search is fast, and near-duplicate removal stops the
    # extra candidates from turning into redundant context.
    candidate_k: int = 30
    min_relevance_score: float = 0.0  # 0 disables the threshold

    # Chunks whose word sets overlap at least this much are treated as the same
    # evidence. Needed because the same document uploaded twice, or as both a PDF
    # and an exported Markdown file, produces text that differs in whitespace and
    # hyphenation - so exact-string dedupe misses it, and the copies then compete
    # for the same top_k slots. Set to 0 to disable the second pass.
    retrieval_near_duplicate_threshold: float = 0.82
    max_context_chars: int = 12000  # hard cap on context sent to the LLM

    # ------------------------------------------------------------ reranker ---
    # OFF by default. Re-ranking can only REORDER candidates that dense
    # retrieval already found - it cannot recover missed evidence. Basic RAG
    # must work without it (see docs/06_advanced_rag.md).
    rerank_enabled: bool = False
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_top_n: int = 5

    # ----------------------------------------------------------- AI modes ---
    default_ai_mode: Literal["online", "offline"] = "online"

    # ------------------------------------------------------ online: Gemini ---
    # Model IDs are defaults only. `GET /api/settings/providers` queries the
    # live provider model list, so a renamed/retired model never breaks the app.
    # Verified against https://ai.google.dev/gemini-api/docs/models (Stable/GA).
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # MODEL CHAIN. Gemini rate-limits per model, so a 429 on one model often
    # leaves the others usable. Trying the next one turns "the provider is down"
    # into "that model is busy". Ordered best-quality first, then lighter
    # variants that carry larger quotas.
    #
    # `gemini_model` stays FIRST so an explicit user choice is respected; the rest
    # are automatic alternates. Set GEMINI_MODELS to a single ID to disable
    # chaining.
    gemini_models: str = (
        "gemini-2.5-flash,"
        "gemini-3.5-flash,"
        "gemini-2.5-flash-lite,"
        "gemini-3.6-flash,"
        "gemini-3.7-flash,"
        "gemini-3.8-flash,"
        "gemini-flash-latest"
    )
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_temperature: float = 0.2
    gemini_max_output_tokens: int = 2048

    # Gemini "thinking" budget.
    #
    # Gemini 2.5+ and 3.x models reason internally before answering, and those
    # reasoning tokens are charged AGAINST maxOutputTokens. Two consequences, both
    # observed in practice on gemini-3.6-flash:
    #
    #   1. A long chain of thought can consume almost the whole budget, so the
    #      visible answer is silently truncated. A Tamil translation came back
    #      missing a citation and four numbers because ~777 tokens had gone on
    #      reasoning, leaving almost nothing for the text.
    #   2. Cost and latency stop being predictable.
    #
    # So we set an explicit budget. `gemini_thinking_budget` is added ON TOP of the
    # output budget, which means `max_tokens` always means "tokens of visible
    # answer" and can never be eaten by reasoning.
    #
    #   0  = thinking disabled. Deterministic, cheapest, and the right default for
    #        mechanical transforms like translation and shortening.
    #   >0 = an explicit reasoning allowance (output budget + this).
    #   -1 = let the model decide (dynamic). Supported, but the least predictable.
    gemini_thinking_budget: int = 0

    gemini_timeout_seconds: float = 90.0

    # -------------------------------------------------------- online: Groq ---
    # Verified against https://console.groq.com/docs/models (Production).
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    # MODEL CHAIN, same rationale as Gemini: a 429 hits one model at a time.
    groq_models: str = (
        "openai/gpt-oss-120b,"
        "groq/compound,"
        "openai/gpt-oss-20b,"
        "qwen/qwen3.8-27b,"
        "groq/compound-mini"
    )
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_temperature: float = 0.2
    groq_max_output_tokens: int = 2048
    groq_timeout_seconds: float = 90.0

    # -------------------------------------------------------- offline: GGUF ---
    # The model shipped in ./models. Verified metadata:
    #   qwen2.5-3b-instruct, Q4_K_M, 36 layers, 32768 ctx, chat template present
    local_model_path: Path = PROJECT_ROOT / "models" / "llm-model.gguf"
    local_model_label: str = "Qwen2.5-3B-Instruct (Q4_K_M)"
    local_n_ctx: int = 8192         # context window actually allocated
    local_n_threads: int = 0        # 0 = auto-detect physical cores
    local_n_gpu_layers: int = 0     # 0 = pure CPU (portable default)
    local_max_tokens: int = 1024
    local_temperature: float = 0.2
    local_top_p: float = 0.9
    local_repeat_penalty: float = 1.1
    local_timeout_seconds: float = 240.0
    local_verbose: bool = False

    # ---------------------------------------------------- offline fail-safe ---
    # When the local LLM cannot load, offline mode must NOT go online. Instead
    # it returns a clearly-labelled extractive answer built only from retrieved
    # evidence. This is honest, and it keeps the offline guarantee intact.
    offline_extractive_failsafe: bool = True

    # --------------------------------------------------------- presentation ---
    translation_enabled: bool = True
    shorten_enabled: bool = True
    read_aloud_enabled: bool = True
    translate_cache_enabled: bool = True
    max_translate_chars: int = 12000

    # ----------------------------------------------------------- web search ---
    # OFF by default and never enabled implicitly. Disabled in offline mode.
    web_search_enabled: bool = False
    web_search_provider: str = "duckduckgo"
    web_search_max_results: int = 5
    web_search_timeout_seconds: float = 15.0

    # ------------------------------------------------------------ grounding ---
    grounding_enabled: bool = True
    grounding_min_overlap: float = 0.18   # lexical support threshold
    require_citations: bool = True

    # -------------------------------------------------------------- limits ---
    max_documents_per_workspace: int = 500
    max_workspaces_per_user: int = 50

    # ------------------------------------------------------------- methods ---
    @field_validator("cors_origins")
    @classmethod
    def _strip_origins(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_path = (self.data_dir / "carinaa.db").as_posix()
        return f"sqlite:///{db_path}"

    def ensure_directories(self) -> None:
        """Create every directory the app writes to. Idempotent."""
        for path in (
            self.data_dir,
            self.upload_dir,
            self.chroma_dir,
            self.embedding_cache_dir,
            self.data_dir / "logs",
        ):
            Path(path).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------- provider availability ---
    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key.strip())

    @property
    def groq_configured(self) -> bool:
        return bool(self.groq_api_key.strip())

    @property
    def local_model_present(self) -> bool:
        try:
            return Path(self.local_model_path).is_file()
        except OSError:
            return False

    def local_model_size_gb(self) -> float:
        try:
            return round(Path(self.local_model_path).stat().st_size / (1024**3), 2)
        except OSError:
            return 0.0

    def redacted_summary(self) -> dict:
        """Safe-to-serve configuration summary.

        Deliberately omits every secret. This is what the Settings page shows,
        and what we are willing to send to the browser.
        """
        return {
            "app": {
                "name": self.app_name,
                "tagline": self.app_tagline,
                "version": self.app_version,
                "environment": self.environment,
            },
            "ingestion": {
                "chunk_size": self.chunk_size,
                "chunk_overlap": self.chunk_overlap,
                "min_chunk_chars": self.min_chunk_chars,
            },
            "embeddings": {
                "model": self.embedding_model,
                "local": True,
                "batch_size": self.embedding_batch_size,
            },
            "retrieval": {
                "top_k": self.top_k,
                "candidate_k": self.candidate_k,
                "min_relevance_score": self.min_relevance_score,
                "near_duplicate_threshold": self.retrieval_near_duplicate_threshold,
                "max_context_chars": self.max_context_chars,
            },
            "rerank": {
                "enabled": self.rerank_enabled,
                "model": self.rerank_model,
                "top_n": self.rerank_top_n,
            },
            "modes": {
                "default": self.default_ai_mode,
                "offline_extractive_failsafe": self.offline_extractive_failsafe,
            },
            "providers": {
                "groq": {
                    "configured": self.groq_configured,
                    "model": self.groq_model,
                    "role": "primary",
                },
                "gemini": {
                    "configured": self.gemini_configured,
                    "model": self.gemini_model,
                    "role": "fallback",
                },
                "local": {
                    "available": self.local_model_present,
                    "model": self.local_model_label,
                    "path": str(self.local_model_path),
                    "size_gb": self.local_model_size_gb(),
                    "context": self.local_n_ctx,
                    "gpu_layers": self.local_n_gpu_layers,
                    "role": "offline",
                },
            },
            "presentation": {
                "translation": self.translation_enabled,
                "shorten": self.shorten_enabled,
                "read_aloud": self.read_aloud_enabled,
            },
            "web_search": {
                "enabled": self.web_search_enabled,
                "note": "Optional and never activated implicitly. Disabled in offline mode.",
            },
            "grounding": {
                "enabled": self.grounding_enabled,
                "require_citations": self.require_citations,
            },
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    settings = Settings()
    settings.ensure_directories()
    return settings


def reload_settings() -> Settings:
    """Drop the cache and rebuild (used by tests)."""
    get_settings.cache_clear()
    return get_settings()


# Convenience alias used across the codebase.
settings = get_settings()


def _warn_if_insecure() -> None:
    """Emit a single loud warning when running with unsafe defaults."""
    if settings.environment == "production":
        if not os.environ.get("SECRET_KEY"):
            raise RuntimeError(
                "SECRET_KEY must be set explicitly when ENVIRONMENT=production."
            )


_warn_if_insecure()
