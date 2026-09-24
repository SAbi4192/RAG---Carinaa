"""
The RAG query pipeline.

THE FULL PATH (spec section 5, 69)
----------------------------------
    Question
      |
    Query Processing        normalise, detect language, extract keywords
      |
    Query Embedding         same local model as the chunks
      |
    Vector Search           workspace-scoped cosine search in Chroma
      |
    Candidate Retrieval     join SQLite, dedupe, threshold
      |
    Optional Re-ranking     cross-encoder, off by default
      |
    Context Building        number, order, budget the excerpts
      |
    LLM Generation          via the adapter (online or offline)
      |
    Grounding               is the answer supported by the evidence?
      |
    Citation Resolution     map [n] back to real document locations
      |
    Answer                  canonical, stored once, never overwritten

DESIGN RULES THIS FILE OBEYS
----------------------------
1. Retrieval and grounding know NOTHING about translation, shortening or read
   aloud. Those are presentation layers applied afterwards (spec section 2).
2. The answer produced here is the CANONICAL answer. Translation and shortening
   produce variants; they never overwrite this (spec section 60).
3. The trace is built from real measurements as the pipeline runs, not
   reconstructed afterwards.
4. If retrieval returns nothing useful, we do not ask the model to improvise. We
   either let it refuse (which grounding labels INSUFFICIENT_EVIDENCE) or, in
   offline mode with no local model, we use the labelled extractive fail-safe.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Sequence

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import CarinaaError, LLMError, LocalModelUnavailable, RetrievalError
from app.core.logging import get_logger
from app.llm.adapter import get_llm_adapter
from app.llm.base import LLMResponse
from app.rag.citations import (
    CitationReport,
    normalize_citation_markers,
    resolve_citations,
)
from app.rag.context import ContextBundle, build_context, context_stats
from app.rag.failsafe import build_extractive_answer
from app.rag.grounding import GroundingResult, check_grounding
from app.rag.prompts import build_generation_messages
from app.rag.retriever import RetrievalOutcome, get_retriever
from app.rag.units import expand_unit_reference
from app.rag.understanding import (
    build_rewrite_messages,
    clean_rewrite,
    needs_conversation_context,
)
from app.rag.trace import TraceRecorder

logger = get_logger(__name__)


@dataclass
class RAGAnswer:
    """The complete, canonical result of one question."""

    question: str
    answer: str
    mode: str

    # Honest generation provenance
    provider: str = ""
    model: str = ""
    used_fallback: bool = False
    fallback_reason: str = ""
    primary_attempted: str = ""
    is_extractive_failsafe: bool = False
    generation_ms: int = 0
    total_ms: int = 0
    token_usage: dict[str, Any] = field(default_factory=dict)

    # Retrieval + grounding
    retrieval: RetrievalOutcome | None = None
    context: ContextBundle | None = None
    grounding: GroundingResult | None = None
    citations: CitationReport | None = None
    trace: TraceRecorder | None = None

    # Optional web augmentation
    web_search_used: bool = False
    web_sources: list[dict[str, Any]] = field(default_factory=list)

    error: str = ""
    error_code: str = ""

    @property
    def citation_dicts(self) -> list[dict[str, Any]]:
        return self.citations.as_dict()["citations"] if self.citations else []

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "provider_label": self.provider_label(),
            "used_fallback": self.used_fallback,
            "fallback_reason": self.fallback_reason,
            "primary_attempted": self.primary_attempted,
            "is_extractive_failsafe": self.is_extractive_failsafe,
            "generation_ms": self.generation_ms,
            "total_ms": self.total_ms,
            "token_usage": self.token_usage,
            "grounding": self.grounding.as_dict() if self.grounding else None,
            "citations": self.citations.as_dict() if self.citations else None,
            "retrieval": self.retrieval.as_dict() if self.retrieval else None,
            "context": (
                {**self.context.as_dict(), "stats": context_stats(self.context)}
                if self.context
                else None
            ),
            "trace": self.trace.summary() if self.trace else None,
            "web_search_used": self.web_search_used,
            "web_sources": self.web_sources,
            "error": self.error,
            "error_code": self.error_code,
        }

    def provider_label(self) -> str:
        """What the UI shows. A fallback is never disguised as the primary."""
        if self.is_extractive_failsafe:
            return "Extractive (no model)"
        if self.provider == "local":
            return f"Local · {settings.local_model_label}"
        if self.used_fallback:
            return f"{self.provider.title()} · Fallback"
        return self.provider.title() if self.provider else "Unknown"


@dataclass
class _Prepared:
    """Everything retrieval and context building produced, before generation.

    Shared by `answer` and `stream_answer` so the two paths cannot drift: whatever
    question rewriting, filtering, retrieval and context the non-streaming answer
    performs, the streaming answer performs identically.
    """

    retrieval: RetrievalOutcome
    bundle: ContextBundle
    prompt_excerpts: list[dict[str, Any]]
    evidence_excerpts: list[dict[str, Any]]
    resolved_question: str
    followup_detected: bool
    rewrite_note: str
    unit_note: str


class RAGPipeline:
    """Runs one question through the whole pipeline."""

    def __init__(self) -> None:
        self.retriever = get_retriever()
        self.adapter = get_llm_adapter()

    # =====================================================================
    # Shared retrieval + context preparation
    # =====================================================================
    async def _prepare(
        self,
        db: Session,
        *,
        workspace_id: int,
        question: str,
        result: RAGAnswer,
        mode: str,
        top_k: int | None,
        candidate_k: int | None,
        document_ids: Sequence[int] | None,
        use_rerank: bool | None,
        trace: TraceRecorder,
        history: list[dict[str, str]] | None,
        page_number: int | None,
        section: str | None,
    ) -> _Prepared:
        """Understand the question, retrieve, and build the context bundle.

        The two entry points (`answer`, `stream_answer`) both call this, so the
        evidence fed to the model is identical whichever way it is generated.
        """
        # A follow-up like "what is my name?" or "explain the second one" cannot be
        # answered, or even retrieved for, in isolation. This resolves it into a
        # standalone question using the conversation.
        resolved_question = question
        rewrite_note = ""
        followup_detected = bool(
            history and needs_conversation_context(question, len(history))
        )
        if followup_detected and mode == "online":
            try:
                rewrite_response = await self.adapter.generate(
                    build_rewrite_messages(question, history),
                    mode=mode,
                    temperature=0.0,
                    max_tokens=80,
                )
                candidate = clean_rewrite(rewrite_response.text, question)
                if candidate and candidate.strip() != question.strip():
                    resolved_question = candidate
                    rewrite_note = "Question rewritten using the conversation."
            except Exception as exc:  # noqa: BLE001 - a failed rewrite is not fatal
                logger.warning("Query rewrite skipped: %s", exc.__class__.__name__)

        unit_note = ""
        if resolved_question:
            expanded, unit_note = expand_unit_reference(resolved_question)
            if unit_note:
                resolved_question = expanded

        if resolved_question != question:
            result.question = resolved_question

        # ------------------------------------------------------------- retrieval
        try:
            retrieval: RetrievalOutcome = await asyncio.to_thread(
                self.retriever.retrieve,
                db,
                workspace_id=workspace_id,
                question=resolved_question,
                top_k=top_k,
                candidate_k=candidate_k,
                page_number=page_number,
                section=section,
                document_ids=document_ids,
                use_rerank=use_rerank,
                trace=trace,
            )
        except CarinaaError as exc:
            result.error = exc.message
            result.error_code = exc.code
            raise

        result.retrieval = retrieval

        # ------------------------------------------------------ context building
        with trace.stage("context_building") as info:
            bundle = build_context(retrieval.chunks)
            stats = context_stats(bundle)
            info.update(stats)
        result.context = bundle

        # Two views of the SAME evidence, and the difference matters: the prompt
        # list is minimal for the model, the evidence list carries provenance that
        # citation resolution and grounding need. See the long note in git history.
        prompt_excerpts = bundle.as_prompt_list()
        evidence_excerpts = bundle.as_dict()["excerpts"]

        return _Prepared(
            retrieval=retrieval,
            bundle=bundle,
            prompt_excerpts=prompt_excerpts,
            evidence_excerpts=evidence_excerpts,
            resolved_question=resolved_question,
            followup_detected=followup_detected,
            rewrite_note=rewrite_note,
            unit_note=unit_note,
        )

    def _finalize(
        self,
        result: RAGAnswer,
        prepared: _Prepared,
        *,
        question: str,
        web_sources: list[dict[str, Any]] | None,
        started: float,
    ) -> None:
        """Marker normalisation, citation resolution, grounding, trace annotation.

        Runs identically after a buffered or a streamed completion: the SAME text
        is cited and grounded, so a streamed answer is not a second-class one.
        """
        # Providers disagree about citation brackets (ASCII "[1]" vs the full-width
        # CJK form). Canonicalise once so the stored answer, its citations, the
        # trace, read-aloud and the validators all read one form.
        normalized_answer = normalize_citation_markers(result.answer)
        if normalized_answer != result.answer:
            logger.info("Normalised non-ASCII citation brackets in the generated answer.")
            result.answer = normalized_answer

        evidence_excerpts = prepared.evidence_excerpts

        with result.trace.stage("citation_resolution") as info:  # type: ignore[union-attr]
            citation_report = resolve_citations(
                result.answer, evidence_excerpts, web_sources=web_sources
            )
            info.update(
                {
                    "citations_found": len(citation_report.citations),
                    "cited_numbers": citation_report.cited_numbers,
                    "invalid_numbers": citation_report.invalid_numbers,
                    "unused_excerpts": citation_report.unused_numbers,
                    "valid": citation_report.valid,
                }
            )
        result.citations = citation_report

        with result.trace.stage("grounding") as info:  # type: ignore[union-attr]
            grounding = check_grounding(
                result.answer,
                evidence_excerpts,
                citation_report,
                top_score=prepared.retrieval.top_score,
            )
            info.update(
                {
                    "status": grounding.status,
                    "refused": grounding.refused,
                    "supported_sentences": grounding.supported_count,
                    "weak_sentences": grounding.weak_count,
                    "uncited_sentences": grounding.uncited_count,
                    "checks": {
                        name: check.get("passed")
                        for name, check in grounding.checks.items()
                    },
                }
            )
        result.grounding = grounding

        # Annotate the trace once the run is complete. This has to happen HERE:
        # `query_analysis` is recorded during retrieval, so writing to it before the
        # pipeline ran silently did nothing.
        understanding_notes: dict[str, Any] = {}
        if prepared.followup_detected:
            understanding_notes.update(
                {
                    "original_question": question,
                    "resolved_question": prepared.resolved_question,
                    "is_followup": True,
                    "rewrite_reason": prepared.rewrite_note
                    or "The question needed the conversation; it was already "
                    "self-contained so the wording was left unchanged.",
                }
            )
        if prepared.unit_note:
            understanding_notes["unit_reference"] = prepared.unit_note

        if understanding_notes and result.trace:
            for event in result.trace.events:
                if event.stage == "query_analysis":
                    event.data.update(understanding_notes)
                    break

        result.total_ms = int((time.perf_counter() - started) * 1000)

    async def answer(
        self,
        db: Session,
        *,
        workspace_id: int,
        question: str,
        mode: str = "online",
        top_k: int | None = None,
        candidate_k: int | None = None,
        document_ids: Sequence[int] | None = None,
        use_rerank: bool | None = None,
        web_sources: list[dict[str, Any]] | None = None,
        language: str = "en",
        trace: TraceRecorder | None = None,
        history: list[dict[str, str]] | None = None,
        page_number: int | None = None,
        section: str | None = None,
        understanding: dict[str, Any] | None = None,
    ) -> RAGAnswer:
        """Produce a grounded, cited answer."""
        started = time.perf_counter()
        trace = trace or TraceRecorder()
        result = RAGAnswer(question=question, answer="", mode=mode, trace=trace)

        prepared = await self._prepare(
            db,
            workspace_id=workspace_id,
            question=question,
            result=result,
            mode=mode,
            top_k=top_k,
            candidate_k=candidate_k,
            document_ids=document_ids,
            use_rerank=use_rerank,
            trace=trace,
            history=history,
            page_number=page_number,
            section=section,
        )

        # =================================================================
        # GENERATION
        # =================================================================
        generation_started = time.perf_counter()
        try:
            llm_response: LLMResponse = await self.adapter.generate(
                build_generation_messages(
                    question,
                    prepared.prompt_excerpts,
                    history=history,
                    language=language,
                    web_sources=web_sources,
                    mode=mode,
                ),
                mode=mode,
                trace=trace,
            )
            result.answer = llm_response.text
            result.provider = llm_response.provider
            result.model = llm_response.model
            result.used_fallback = llm_response.used_fallback
            result.fallback_reason = llm_response.fallback_reason
            result.primary_attempted = llm_response.primary_attempted
            result.token_usage = llm_response.token_usage

        except (LocalModelUnavailable, LLMError) as exc:
            self._offline_failsafe(
                result, exc, question=question, mode=mode, prepared=prepared,
                started=started, generation_started=generation_started,
            )

        result.generation_ms = int((time.perf_counter() - generation_started) * 1000)
        self._finalize(
            result, prepared, question=question, web_sources=web_sources, started=started
        )
        return result

    async def stream_answer(
        self,
        db: Session,
        *,
        workspace_id: int,
        question: str,
        mode: str = "online",
        top_k: int | None = None,
        candidate_k: int | None = None,
        document_ids: Sequence[int] | None = None,
        use_rerank: bool | None = None,
        web_sources: list[dict[str, Any]] | None = None,
        language: str = "en",
        trace: TraceRecorder | None = None,
        history: list[dict[str, str]] | None = None,
        page_number: int | None = None,
        section: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Answer a question, yielding REAL text deltas as the provider produces them.

        Yields dicts:
            {"type": "delta", "text": "..."}     a real provider fragment
            {"type": "done", "result": RAGAnswer} the finished, cited, grounded answer

        Errors propagate as exceptions, exactly as `answer` does. The caller (the
        SSE route) catches them and reports the failure, including the trace that
        was recorded up to that point - never a completed pipeline for a run that
        produced no answer.

        The deltas are NOT a chopped-up finished answer: each one is emitted the
        moment the provider sends it, and the stages recorded during retrieval are
        already in the trace when the first delta is yielded.
        """
        started = time.perf_counter()
        trace = trace or TraceRecorder()
        result = RAGAnswer(question=question, answer="", mode=mode, trace=trace)

        prepared = await self._prepare(
            db,
            workspace_id=workspace_id,
            question=question,
            result=result,
            mode=mode,
            top_k=top_k,
            candidate_k=candidate_k,
            document_ids=document_ids,
            use_rerank=use_rerank,
            trace=trace,
            history=history,
            page_number=page_number,
            section=section,
        )

        messages = build_generation_messages(
            question,
            prepared.prompt_excerpts,
            history=history,
            language=language,
            web_sources=web_sources,
            mode=mode,
        )

        generation_started = time.perf_counter()
        meta: dict[str, Any] = {}
        parts: list[str] = []

        try:
            async for fragment in self.adapter.stream(messages, mode=mode, meta=meta):
                parts.append(fragment)
                yield {"type": "delta", "text": fragment}

            result.answer = "".join(parts)
            result.provider = str(meta.get("provider", ""))
            result.model = str(meta.get("model", ""))
            result.used_fallback = bool(meta.get("used_fallback"))
            result.fallback_reason = str(meta.get("fallback_reason", ""))
            result.primary_attempted = str(meta.get("primary_attempted", ""))
            # Usage is not reported by a streaming provider, so nothing is invented:
            # the trace records that the run was streamed instead of a token count.
            result.token_usage = {"streamed": True}

            if trace:
                trace.add(
                    "llm_generation",
                    duration_ms=int((time.perf_counter() - generation_started) * 1000),
                    data={
                        "mode": mode,
                        "provider": result.provider,
                        "model": result.model,
                        "role": meta.get("role", "primary"),
                        "used_fallback": result.used_fallback,
                        "streamed": True,
                        "note": (
                            "The answer was streamed token by token as the model "
                            "produced it. Token counts are not reported by a "
                            "streaming provider, so none are shown."
                        ),
                        **(
                            {"fallback_reason": result.fallback_reason}
                            if result.fallback_reason
                            else {}
                        ),
                    },
                )
        except (LocalModelUnavailable, LLMError) as exc:
            # Only recoverable if NOTHING has been streamed yet. If text already
            # reached the client, the caller reports the failure and the partial
            # text is discarded rather than presented as a complete answer.
            if parts:
                raise
            self._offline_failsafe(
                result, exc, question=question, mode=mode, prepared=prepared,
                started=started, generation_started=generation_started,
            )
            yield {"type": "delta", "text": result.answer}

        result.generation_ms = int((time.perf_counter() - generation_started) * 1000)

        self._finalize(
            result, prepared, question=question, web_sources=web_sources, started=started
        )
        yield {"type": "done", "result": result}

    def _offline_failsafe(
        self,
        result: RAGAnswer,
        exc: Exception,
        *,
        question: str,
        mode: str,
        prepared: _Prepared,
        started: float,
        generation_started: float,
    ) -> None:
        """The ONLY place a degraded answer is produced, and it is local.

        We never fall back to an online provider, ever - see app/llm/adapter.py.
        """
        if mode == "offline" and settings.offline_extractive_failsafe:
            logger.warning("Local model unavailable; using the extractive fail-safe.")
            trace = result.trace
            with trace.stage("failsafe") as info:  # type: ignore[union-attr]
                text, meta = build_extractive_answer(question, prepared.evidence_excerpts)
                info.update(
                    {
                        "reason": str(exc),
                        "sentences_quoted": meta.get("sentences", 0),
                        "used_language_model": False,
                        "contacted_online_service": False,
                    }
                )
            result.answer = text
            result.provider = "local"
            result.model = "extractive"
            result.is_extractive_failsafe = True
            result.fallback_reason = str(exc)
            return

        result.error = str(exc)
        result.error_code = getattr(exc, "code", "llm_error")
        result.generation_ms = int((time.perf_counter() - generation_started) * 1000)
        result.total_ms = int((time.perf_counter() - started) * 1000)
        raise exc

    # =====================================================================
    # Introspection
    # =====================================================================
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<RAGPipeline>"


_pipeline: RAGPipeline | None = None


def get_rag_pipeline() -> RAGPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = RAGPipeline()
    return _pipeline


# ---------------------------------------------------------------------------
# Convenience for the Playground page: retrieval only, no generation
# ---------------------------------------------------------------------------
async def retrieve_only(
    db: Session,
    *,
    workspace_id: int,
    question: str,
    top_k: int | None = None,
    candidate_k: int | None = None,
    document_ids: Sequence[int] | None = None,
    page_number: int | None = None,
    section: str | None = None,
    use_rerank: bool | None = None,
) -> dict[str, Any]:
    """Run retrieval and context building, but never call an LLM.

    This is what makes the Playground genuinely useful for teaching: you can show
    the retrieval step in isolation, with real scores, without paying for or
    waiting on a generation.
    """
    trace = TraceRecorder()
    retriever = get_retriever()
    try:
        outcome = await asyncio.to_thread(
            retriever.retrieve,
            db,
            workspace_id=workspace_id,
            question=question,
            page_number=page_number,
            section=section,
            top_k=top_k,
            candidate_k=candidate_k,
            document_ids=document_ids,
            use_rerank=use_rerank,
            trace=trace,
        )
    except RetrievalError as exc:
        return {"error": exc.message, "error_code": exc.code}

    bundle = build_context(outcome.chunks)
    return {
        "retrieval": outcome.as_dict(),
        "context": {**bundle.as_dict(), "stats": context_stats(bundle)},
        "trace": trace.summary(),
        "generated": False,
        "note": "Retrieval only. No language model was called.",
    }
