"""
Optional web search.

RULES THIS MODULE OBEYS (spec section 52)
-----------------------------------------
  * Web search is OFF by default and never activated implicitly.
  * Offline mode disables it completely - no exceptions.
  * Document sources and web sources are kept visibly separate in the UI, so the
    user always knows which knowledge an answer came from.
  * RAG-only mode stays purely document-grounded.

WHY DUCKDUCKGO
--------------
It needs no API key, which means a student can demonstrate this feature without
signing up for anything. That is the right trade-off for this project.

The trade-off we accept: it parses HTML, so a markup change upstream could break
it. When that happens the module returns zero results and says so - it never
silently returns stale or invented data.
"""

from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_ENDPOINT = "https://html.duckduckgo.com/html/"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_RESULT_BLOCK = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r"(?P<rest>.*?)(?=<a[^>]+class=\"result__a\"|</div>\s*</div>\s*</div>|$)",
    re.DOTALL,
)
_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class WebResult:
    number: int
    title: str
    url: str
    snippet: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "marker": f"[W{self.number}]",
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "kind": "web",
        }


@dataclass
class WebSearchOutcome:
    results: list[WebResult] = field(default_factory=list)
    query: str = ""
    enabled: bool = False
    error: str = ""
    duration_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "query": self.query,
            "count": len(self.results),
            "error": self.error,
            "duration_ms": self.duration_ms,
            "results": [r.as_dict() for r in self.results],
        }


def _clean(fragment: str) -> str:
    text = _TAGS.sub("", fragment or "")
    return html.unescape(" ".join(text.split())).strip()


def _unwrap_redirect(url: str) -> str:
    """DuckDuckGo wraps results in a redirect. Extract the real target."""
    if "duckduckgo.com/l/" not in url and "uddg=" not in url:
        return url
    parsed = urllib.parse.urlparse(url if url.startswith("http") else f"https:{url}")
    params = urllib.parse.parse_qs(parsed.query)
    target = params.get("uddg", [""])[0]
    return urllib.parse.unquote(target) if target else url


def search(query: str, *, max_results: int | None = None) -> WebSearchOutcome:
    """Run a web search. Returns an empty result set on any failure."""
    outcome = WebSearchOutcome(query=query, enabled=settings.web_search_enabled)

    if not settings.web_search_enabled:
        outcome.error = "Web search is disabled on the server."
        return outcome

    if not query.strip():
        outcome.error = "No search query was provided."
        return outcome

    limit = max_results or settings.web_search_max_results

    import time

    started = time.perf_counter()

    try:
        body = urllib.parse.urlencode({"q": query}).encode()
        request = urllib.request.Request(
            _ENDPOINT,
            data=body,
            headers={
                "User-Agent": _USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept-Language": "en-US,en;q=0.9",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=settings.web_search_timeout_seconds) as resp:
            page = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Web search failed: %s", exc)
        outcome.error = (
            "The web search provider could not be reached. Document-only results are "
            "unaffected."
        )
        outcome.duration_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    results: list[WebResult] = []
    for match in _RESULT_BLOCK.finditer(page):
        url = _unwrap_redirect(html.unescape(match.group("url")))
        title = _clean(match.group("title"))
        if not title or not url.startswith("http"):
            continue

        snippet_match = _SNIPPET.search(match.group("rest") or "")
        snippet = _clean(snippet_match.group("snippet")) if snippet_match else ""

        results.append(
            WebResult(number=len(results) + 1, title=title[:200], url=url, snippet=snippet[:500])
        )
        if len(results) >= limit:
            break

    outcome.duration_ms = int((time.perf_counter() - started) * 1000)
    outcome.results = results

    if not results:
        outcome.error = (
            "No web results were returned. The search provider may have changed its "
            "response format."
        )

    return outcome


def is_allowed(mode: str, requested: bool) -> tuple[bool, str]:
    """Decide whether a web search may run. Returns (allowed, reason)."""
    if not requested:
        return False, "Web search was not requested for this question."

    if mode == "offline":
        return False, (
            "Web search is unavailable in Offline mode. Offline mode performs no "
            "network requests of any kind."
        )

    if not settings.web_search_enabled:
        return False, (
            "Web search is disabled on the server. Enable WEB_SEARCH_ENABLED in .env "
            "to allow it."
        )

    return True, ""
