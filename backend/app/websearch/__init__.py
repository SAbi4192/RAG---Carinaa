"""Optional web search augmentation."""

from app.websearch.provider import WebResult, WebSearchOutcome, is_allowed, search

__all__ = ["WebResult", "WebSearchOutcome", "is_allowed", "search"]
