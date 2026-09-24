"""
Evidence Pack export.

A "pack" is one answer rendered out with everything that made it: the question,
the answer text, the grounding verdict, the cited sources (deep-linked back to
the exact chunk), the full retrieved evidence, the trace with real per-stage
durations, and the provider that produced it.

THE RULE THIS FILE IS BUILT AROUND
----------------------------------
Everything here is assembled from ALREADY-STORED data - the Message row and its
TraceEvent rows. The export NEVER re-runs the pipeline.

That is not laziness, it is honesty. A re-run would retrieve similar-but-not-
identical context and a language model would produce different wording, so the
export would bear a plausible answer that was not the answer the user actually
saw and judged. The whole product promise is "the answer you can trace"; an
export that regenerated the trace to look complete would break that promise at
the exact moment someone carries the result into a report. If a stage was not
recorded, it is absent here, not back-filled.

WHAT IS DELIBERATELY LEFT OUT
-----------------------------
A real PDF file. Rendering a PDF server-side needs a layout engine the project
does not depend on, and a hand-rolled one would produce a broken document.
Instead this module emits print-ready HTML (the browser's "Save as PDF" is a
faithful, standard renderer), and the route labels it for what it is. No file is
claiming to be a PDF that is only approximately one.
"""

from __future__ import annotations

import html
from typing import Any, Iterable
from urllib.parse import urlsplit


# The grounding statuses the reader cares about, mapped to a plain label. These
# mirror app/rag/grounding.py; kept as a small literal so the export has no
# import-time coupling to the retrieval stack.
_GROUNDING_LABEL = {
    "SUPPORTED": "Supported by the retrieved evidence",
    "PARTIALLY_SUPPORTED": "Partially supported by the retrieved evidence",
    "INSUFFICIENT_EVIDENCE": "Not enough evidence in the documents",
    "": "Not assessed",
}


def _fmt_dt(value: Any) -> str:
    """Render a datetime-ish value as a readable timestamp, never a fake one."""
    if value is None:
        return "unknown"
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    return str(value)


def _safe_url(value: str | None) -> str | None:
    """Validate a URL scheme before it goes into an `href` in the exported file.

    The export is meant to be shared and opened by other people (an examiner, a
    teammate), so a `base_url` or stored citation URL must not be able to turn
    a link into `javascript:...` or an open redirect. Only http/https is
    accepted, and credentials are dropped (a URL with an embedded
    `user:pass@` belongs masking nothing - it belongs outright rejected).
    """
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.username or parsed.password:
        return None
    return value


def _citation_link(citation: dict[str, Any], base_url: str | None) -> str | None:
    """A link to the exact chunk in the app's document viewer, if it is linkable.

    A citation to a web source, or to a chunk we cannot locate, links nowhere -
    the export does not invent a target. `base_url` is validated for scheme, so
    neither a crafted query parameter nor a stored citation URL can smuggle a
    `javascript:` or non-https target into the exported document.
    """
    document_id = citation.get("document_id")
    chunk_id = citation.get("chunk_id")
    if not document_id or not chunk_id:
        return None
    path = f"/app/knowledge/{document_id}?chunk={chunk_id}"
    safe_base = _safe_url(base_url)
    return f"{safe_base}{path}" if safe_base else path


def _location(citation: dict[str, Any]) -> str:
    parts: list[str] = []
    section = citation.get("section")
    if section:
        parts.append(str(section))
    page = citation.get("page_number")
    end = citation.get("page_end")
    if page is not None:
        parts.append(f"p.{page}" if page == end or end is None else f"pp.{page}-{end}")
    if citation.get("slide_number") is not None:
        parts.append(f"slide {citation['slide_number']}")
    if citation.get("sheet_name"):
        parts.append(str(citation["sheet_name"]))
    if citation.get("chunk_index") is not None:
        parts.append(f"chunk #{citation['chunk_index']}")
    return " · ".join(parts) or "—"


def build_markdown(
    *,
    question: str,
    answer: str,
    provider: str,
    model: str,
    used_fallback: bool,
    fallback_reason: str,
    is_extractive: bool,
    ai_mode: str,
    grounding: dict[str, Any] | None,
    citations: Iterable[dict[str, Any]],
    retrieval: dict[str, Any] | None,
    web_sources: Iterable[dict[str, Any]],
    trace_events: Iterable[dict[str, Any]],
    created_at: Any,
    latency_ms: int,
    base_url: str | None = None,
) -> str:
    """Render the whole evidence pack as a single Markdown document."""
    citations = list(citations or [])
    web_sources = list(web_sources or [])
    trace_events = list(trace_events or [])
    chunks = (retrieval or {}).get("chunks", []) if isinstance(retrieval, dict) else []

    lines: list[str] = []
    lines.append("# Carinaa — Evidence Pack")
    lines.append("")
    lines.append(
        "_Generated from the stored answer and its recorded trace. No part of this "
        "was re-run or regenerated for export._"
    )
    lines.append("")
    lines.append(f"**Asked:** {_fmt_dt(created_at)}")
    lines.append(f"**Answer time:** {latency_ms} ms")
    model_label = f"{provider}" + (f" · {model}" if model else "")
    if used_fallback:
        model_label += " (fallback)"
    if is_extractive:
        model_label = "Extractive (no language model)"
    lines.append(f"**Source AI:** {model_label} · {ai_mode} mode")
    lines.append("")

    lines.append("## Question")
    lines.append("")
    lines.append(f"> {question.strip() or '—'}")
    lines.append("")

    lines.append("## Answer")
    lines.append("")
    lines.append((answer or "").strip() or "_The answer was empty._")
    lines.append("")

    if grounding:
        status = grounding.get("status", "")
        lines.append("## Grounding")
        lines.append("")
        lines.append(
            f"**Verdict:** {_GROUNDING_LABEL.get(status, status or 'Not assessed')}"
        )
        if grounding.get("reason"):
            lines.append(f"- {grounding['reason']}")
        counts = grounding.get("counts", {}) or {}
        if counts:
            lines.append(
                "- Sentence support: "
                f"{counts.get('supported', 0)} supported, "
                f"{counts.get('weak', 0)} weak, "
                f"{counts.get('uncited', 0)} uncited"
            )
        if grounding.get("refused"):
            lines.append("- This answer refused to claim certainty (flagged as a refusal).")
        lines.append("")

    if citations:
        lines.append("## Cited sources")
        lines.append("")
        for citation in citations:
            number = citation.get("number", "?")
            name = citation.get("document_name") or citation.get("url") or "source"
            kind = "Web" if citation.get("kind") == "web" else "Document"
            line = f"{number}. **[{number}]** {name} — _{kind}, {_location(citation)}_"
            link = _citation_link(citation, base_url)
            if link:
                line += f"\n   [Open the exact passage →]({link})"
            else:
                safe_url = _safe_url(citation.get("url"))
                if safe_url:
                    line += f"\n   {safe_url}"
            snippet = (citation.get("snippet") or "").strip()
            if snippet:
                quoted = "\n   > ".join(snippet.splitlines())
                line += f"\n   > {quoted[:700]}"
            lines.append(line)
        lines.append("")

    if chunks:
        lines.append("## Retrieved evidence")
        lines.append("")
        scale = (retrieval or {}).get("score_scale", "")
        mode = (retrieval or {}).get("mode", "")
        header = f"_{len(chunks)} passages were retrieved and ranked"
        if mode:
            header += f" by the **{mode}** retriever"
        if scale:
            header += f" (scores are on the {scale} scale)"
        lines.append(header + ":_")
        lines.append("")
        for chunk in chunks:
            label = chunk.get("label") or f"#{chunk.get('chunk_id')}"
            doc = chunk.get("document_name") or "document"
            score = chunk.get("score")
            score_text = f" · score {round(float(score), 4)}" if isinstance(score, (int, float)) else ""
            lines.append(f"**[{label}]** {doc}{score_text}")
            body = (chunk.get("content") or "").strip()
            if body:
                lines.append("")
                lines.append("> " + body.replace("\n", "\n> ")[:1200])
            lines.append("")

    if web_sources:
        lines.append("## Web sources used")
        lines.append("")
        lines.append(
            "_These were fetched live and are separate from the documents; "
            "they are marked so a reader can tell external evidence apart from "
            "the workspace._"
        )
        lines.append("")
        for source in web_sources:
            title = source.get("title") or source.get("url") or "source"
            url = source.get("url") or ""
            lines.append(f"- **{title}** {('— ' + url) if url else ''}")
        lines.append("")

    if trace_events:
        lines.append("## How this answer was produced (RAG trace)")
        lines.append("")
        lines.append(
            "_Every stage below really ran and was timed by the system. A stage with "
            "no row was not executed._"
        )
        lines.append("")
        lines.append("| # | Stage | Status | Time |")
        lines.append("|---:|---|---|---:|")
        for event in trace_events:
            stage = event.get("label") or event.get("stage") or ""
            status = event.get("status", "ok")
            duration = event.get("duration_ms", 0)
            lines.append(
                f"| {event.get('seq', '')} | {stage} | {status} | {duration} ms |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_Carinaa — Every Answer, Traceable._")
    lines.append("")
    return "\n".join(lines)


_CSS = """
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { font: 15px/1.6 -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
         color: #1b1b1f; margin: 0; background: #faf9fc; }
  .sheet { max-width: 820px; margin: 0 auto; padding: 48px 40px 80px; background: #fff;
           box-shadow: 0 1px 40px rgba(40,20,80,.06); }
  h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: -.01em; }
  h2 { font-size: 17px; margin: 34px 0 12px; padding-bottom: 6px; border-bottom: 1px solid #ececf2; color: #4b3fd0; }
  .muted { color: #6a6a78; font-size: 13px; }
  .meta { font-size: 13px; color: #55555f; display: grid; gap: 2px; margin: 10px 0 4px; }
  blockquote { margin: 10px 0; padding: 8px 14px; border-left: 3px solid #cfc8ff; background: #f7f6ff; color: #333; white-space: pre-wrap; }
  .answer { white-space: pre-wrap; }
  .pill { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 12px; font-weight: 600; }
  .ok { background: #e6f7ee; color: #177a41; }
  .warn { background: #fff3e0; color: #9a5b00; }
  .bad { background: #fdecec; color: #a4262c; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 8px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #ececf2; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; color: #55555f; }
  ol.sources { padding-left: 20px; }
  ol.sources li { margin: 10px 0; }
  .src-loc { color: #6a6a78; font-size: 12.5px; }
  a { color: #4b3fd0; text-decoration: none; }
  a:hover { text-decoration: underline; }
  footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid #ececf2; color: #8a8a97; font-size: 12px; }
  @media print { body { background: #fff; } .sheet { box-shadow: none; padding: 0; } .noprint { display: none; } }
"""


def build_html(
    *,
    question: str,
    answer: str,
    provider: str,
    model: str,
    used_fallback: bool,
    is_extractive: bool,
    ai_mode: str,
    grounding: dict[str, Any] | None,
    citations: Iterable[dict[str, Any]],
    retrieval: dict[str, Any] | None,
    web_sources: Iterable[dict[str, Any]],
    trace_events: Iterable[dict[str, Any]],
    created_at: Any,
    latency_ms: int,
    base_url: str | None = None,
) -> str:
    """A print-ready HTML render of the same pack (for Save-as-PDF in a browser)."""
    e = html.escape
    citations = list(citations or [])
    web_sources = list(web_sources or [])
    trace_events = list(trace_events or [])
    chunks = (retrieval or {}).get("chunks", []) if isinstance(retrieval, dict) else []

    def status_pill() -> str:
        if not grounding:
            return ""
        status = grounding.get("status", "")
        cls = {"SUPPORTED": "ok", "PARTIALLY_SUPPORTED": "warn"}.get(status, "bad")
        label = _GROUNDING_LABEL.get(status, status or "Not assessed")
        return f'<span class="pill {cls}">{e(label)}</span>'

    model_label = "Extractive (no language model)" if is_extractive else e(provider or "Unknown")
    if model and not is_extractive:
        model_label += " · " + e(model)
    if used_fallback and not is_extractive:
        model_label += " (fallback)"

    out: list[str] = []
    out.append("<!doctype html><html><head><meta charset='utf-8'>")
    out.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    out.append("<title>Carinaa — Evidence Pack</title>")
    out.append(f"<style>{_CSS}</style></head><body><div class='sheet'>")
    out.append("<h1>Carinaa — Evidence Pack</h1>")
    out.append(
        "<p class='muted'>Assembled from the stored answer and its recorded RAG "
        "trace. Nothing below was re-run for this export.</p>"
    )
    out.append("<div class='meta'>")
    out.append(f"<span><strong>Asked</strong> &nbsp; {e(_fmt_dt(created_at))}</span>")
    out.append(f"<span><strong>Source AI</strong> &nbsp; {model_label} · {e(ai_mode)} mode · {latency_ms} ms</span>")
    out.append("</div>")

    out.append("<h2>Question</h2>")
    out.append(f"<blockquote>{e((question or '').strip() or '—')}</blockquote>")

    out.append("<h2>Answer &nbsp; " + status_pill() + "</h2>")
    out.append(f"<div class='answer'>{e((answer or '').strip())}</div>")

    if grounding and grounding.get("reason"):
        out.append(f"<p class='muted'>{e(grounding['reason'])}</p>")

    if citations:
        out.append("<h2>Cited sources</h2><ol class='sources'>")
        for citation in citations:
            name = citation.get("document_name") or citation.get("url") or "source"
            kind = "Web" if citation.get("kind") == "web" else "Document"
            line = f"<div><strong>[{e(str(citation.get('number','')))}]</strong> {e(str(name))} "
            line += f"<span class='src-loc'>· {kind}, {e(_location(citation))}</span></div>"
            link = _citation_link(citation, base_url)
            target = link or _safe_url(citation.get("url"))
            if target:
                label = "Open the exact passage →" if link else e(str(target))
                line += f"<a href='{e(str(target))}'>{label}</a>"
            snippet = (citation.get("snippet") or "").strip()
            if snippet:
                line += f"<blockquote>{e(snippet[:900])}</blockquote>"
            out.append(f"<li>{line}</li>")
        out.append("</ol>")

    if chunks:
        scale = e(str((retrieval or {}).get("score_scale", "")))
        mode = e(str((retrieval or {}).get("mode", "")))
        out.append("<h2>Retrieved evidence</h2>")
        out.append(
            f"<p class='muted'>{len(chunks)} passages ranked by the {mode or 'retriever'}"
            + (f" (scores on the {scale} scale)" if scale else "")
            + ":</p>"
        )
        for chunk in chunks:
            label = e(str(chunk.get("label") or chunk.get("chunk_id") or ""))
            doc = e(str(chunk.get("document_name") or "document"))
            score = chunk.get("score")
            score_text = (
                f" · {round(float(score), 4)}" if isinstance(score, (int, float)) else ""
            )
            out.append(f"<p><strong>[{label}]</strong> {doc}{score_text}</p>")
            body = (chunk.get("content") or "").strip()
            if body:
                out.append(f"<blockquote>{e(body[:1600])}</blockquote>")

    if web_sources:
        out.append("<h2>Web sources used (live, separate from documents)</h2><ul>")
        for source in web_sources:
            title = e(str(source.get("title") or source.get("url") or "source"))
            url = e(str(source.get("url") or ""))
            out.append(f"<li><strong>{title}</strong> {url}</li>")
        out.append("</ul>")

    if trace_events:
        out.append("<h2>How this answer was produced</h2>")
        out.append("<p class='muted'>Every stage below really ran and was timed by the system.</p>")
        out.append("<table><thead><tr><th>#</th><th>Stage</th><th>Status</th><th>Time</th></tr></thead><tbody>")
        for event in trace_events:
            stage = e(str(event.get("label") or event.get("stage") or ""))
            out.append(
                f"<tr><td class='num'>{e(str(event.get('seq','')))}</td>"
                f"<td>{stage}</td>"
                f"<td>{e(str(event.get('status','ok')))}</td>"
                f"<td class='num'>{event.get('duration_ms',0)} ms</td></tr>"
            )
        out.append("</tbody></table>")

    out.append("<footer>Carinaa — Every Answer, Traceable.</footer>")
    out.append(
        "<p class='noprint muted' style='margin-top:20px'>Tip: use your browser's "
        "Print → Save as PDF to turn this page into a PDF.</p>"
    )
    out.append("</div></body></html>")
    return "".join(out)
