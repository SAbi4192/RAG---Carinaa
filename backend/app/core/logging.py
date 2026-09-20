"""
Logging with secret redaction.

WHY THIS FILE EXISTS
--------------------
Defence in depth for secrets. Even though we never intentionally log an API key,
a careless `logger.info(response.headers)` or an exception carrying a request URL
would leak one. `RedactionFilter` scrubs known secret values and secret-shaped
patterns out of every record before it reaches a handler.

This is a *reduction* of risk, not a guarantee. The primary defence remains:
never give the LLM a secret in the first place.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from typing import Iterable

from app.core.config import settings

# ---------------------------------------------------------------------------
# Patterns that look like credentials regardless of configuration.
# ---------------------------------------------------------------------------
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Google API keys
    (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "AIza***REDACTED***"),
    # Groq / OpenAI-style keys
    (re.compile(r"gsk_[0-9A-Za-z]{20,}"), "gsk_***REDACTED***"),
    (re.compile(r"sk-[0-9A-Za-z\-_]{20,}"), "sk-***REDACTED***"),
    # Authorization headers
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/=]{12,}"), r"\1 ***REDACTED***"),
    # key=... / api_key=... query params
    (
        re.compile(r"(?i)\b(api[_-]?key|key|token|secret|password)\b\s*[=:]\s*[\"']?([^\s\"'&,}]{6,})"),
        r"\1=***REDACTED***",
    ),
    # JWT-ish triplets
    (re.compile(r"\beyJ[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{5,}"), "***JWT REDACTED***"),
)


class RedactionFilter(logging.Filter):
    """Scrub secrets from log messages and arguments."""

    def __init__(self, extra_secrets: Iterable[str] = ()) -> None:
        super().__init__()
        # Only redact non-trivial secret values; short strings cause false hits.
        self._literal: list[str] = sorted(
            {s for s in extra_secrets if s and len(s) >= 8},
            key=len,
            reverse=True,
        )

    def _scrub(self, text: str) -> str:
        for secret in self._literal:
            if secret in text:
                text = text.replace(secret, "***REDACTED***")
        for pattern, replacement in _PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = self._scrub(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: self._scrub(v) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        self._scrub(a) if isinstance(a, str) else a for a in record.args
                    )
        except Exception:  # pragma: no cover - logging must never raise
            return True
        return True


_configured = False


def configure_logging(level: int | None = None) -> None:
    """Install handlers + redaction filter once per process."""
    global _configured
    if _configured:
        return

    log_level = level or (logging.DEBUG if settings.debug else logging.INFO)
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    redactor = RedactionFilter(
        extra_secrets=(settings.gemini_api_key, settings.groq_api_key, settings.secret_key)
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    stream.addFilter(redactor)

    rotating = logging.handlers.RotatingFileHandler(
        log_dir / "carinaa.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    rotating.setFormatter(fmt)
    rotating.addFilter(redactor)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)
    root.addHandler(stream)
    root.addHandler(rotating)

    # Quiet the noisy third-party loggers.
    for noisy in ("httpx", "httpcore", "chromadb", "urllib3", "sentence_transformers", "onnxruntime"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def redact(text: str) -> str:
    """Public helper: scrub a string before it is stored or returned."""
    return RedactionFilter(
        extra_secrets=(settings.gemini_api_key, settings.groq_api_key, settings.secret_key)
    )._scrub(text)


def safe_error_message(exc: BaseException) -> str:
    """Turn an exception into a user-facing message with no secrets or paths.

    Spec section 56: never expose raw stack traces to normal users.
    """
    raw = redact(str(exc) or exc.__class__.__name__)
    # Strip absolute filesystem paths.
    raw = re.sub(r"[A-Za-z]:\\[^\s\"']+", "<path>", raw)
    raw = re.sub(r"/(?:home|Users)/[^\s\"']+", "<path>", raw)
    if len(raw) > 300:
        raw = raw[:297] + "..."
    return raw
