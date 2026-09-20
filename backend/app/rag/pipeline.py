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
from typing import Any, Sequence

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


class RAGPipeline:
    """Runs one question through the whole pipeline."""

    def __init__(self) -> None:
        self.retriever = get_retriever()
        self.adapter = get_llm_adapter()

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

        # =================================================================
        # UNDERSTANDING  (before retrieval - it changes what we search for)
        # =================================================================
        # A follow-up like "what is my name?" or "explain the second one" cannot be
        # answered, or even retrieved for, in isolation. This resolves it into a
        # standalone question using the conversation.
        #
        # Retrieval uses the RESOLVED question; generation uses the ORIGINAL one
        # plus the history. That way retrieval searches for something complete,
        # while the answer is written from what the user actually typed.
        resolved_question = question
        rewrite_note = ""
        # Detection and rewriting are separate facts. A follow-up that the model leaves
        # unchanged is still a follow-up - "What is my name?" is self-contained as a
        # string, yet only answerable from the conversation. Recording only rewrites
        # would hide the cases where history was used but the wording did not change.
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
                # Deliberately broad: ANY problem here must degrade to the original
                # question rather than fail the request. The user asked a question;
                # a helper step going wrong is not their problem.
                logger.warning("Query rewrite skipped: %s", exc.__class__.__name__)

        # A unit reference is expanded rather than filtered. Section metadata is only
        # as complete as the document's headings - a document can have sections for
        # UNIT I, IV and V and none for II or III - so searching the CONTENT for the
        # canonical form works where a filter would find nothing.
        unit_note = ""
        if resolved_question:
            expanded, unit_note = expand_unit_reference(resolved_question)
            if unit_note:
                resolved_question = expanded

        if resolved_question != question:
            result.question = resolved_question



        # =================================================================
        # RETRIEVAL  (blocking work, so it runs off the event loop)
        # =================================================================
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
            result.total_ms = int((time.perf_counter() - started) * 1000)
            raise

        result.retrieval = retrieval

        # =================================================================
        # CONTEXT BUILDING
        # =================================================================
        with trace.stage("context_building") as info:
            bundle = build_context(retrieval.chunks)
            stats = context_stats(bundle)
            info.update(stats)
        result.context = bundle

        # Two views of the SAME evidence, and the difference matters:
        #
        #   prompt_excerpts   only what the model needs to read: number, label, text.
        #                     Keeping the prompt minimal means we are never handing the
        #                     model data it has no use for.
        #
        #   evidence_excerpts the full records - document_id, file name, provenance
        #                     metadata and the real similarity score. Citation
        #                     resolution and grounding NEED these, otherwise a citation
        #                     can only render as "Document None" with a score of 0.0
        #                     even though it resolved to a real chunk.
        #
        # Passing the prompt-shaped list to the citation resolver was a real bug: the
        # `[1]` resolved to a genuine excerpt, but every piece of information that made
        # it *clickable* had been thrown away one line earlier.
        prompt_excerpts = bundle.as_prompt_list()
        evidence_excerpts = bundle.as_dict()["excerpts"]

        # =================================================================
        # GENERATION
        # =================================================================
        generation_started = time.perf_counter()
        try:
            llm_response: LLMResponse = await self.adapter.generate(
                build_generation_messages(
                    question,
                    prompt_excerpts,
                    history=history,
                    language=language,
                    web_sources=web_sources,
                    # Offline gets a shorter prompt written for the 3B local model.
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
            # -----------------------------------------------------------
            # OFFLINE FAIL-SAFE
            # -----------------------------------------------------------
            # This is the ONLY place a degraded answer is produced, and it is
            # produced locally from retrieved evidence. We do not fall back to an
            # online provider, ever - see app/llm/adapter.py.
            if mode == "offline" and settings.offline_extractive_failsafe:
                logger.warning("Local model unavailable; using the extractive fail-safe.")
                with trace.stage("failsafe") as info:
                    text, meta = build_extractive_answer(question, evidence_excerpts)
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
            else:
                result.error = str(exc)
                result.error_code = getattr(exc, "code", "llm_error")
                result.generation_ms = int(
                    (time.perf_counter() - generation_started) * 1000
                )
                result.total_ms = int((time.perf_counter() - started) * 1000)
                raise

        result.generation_ms = int((time.perf_counter() - generation_started) * 1000)

        # =================================================================
        # MARKER NORMALISATION
        # =================================================================
        # Providers disagree about what a citation bracket looks like. Gemini emits
        # ASCII "[1]"; the Groq fallback has been observed emitting the full-width
        # CJK form "【1】". Left alone, that difference silently disabled citation
        # resolution AND grounding: a properly cited answer was reported as citing
        # nothing, and grounding was downgraded to PARTIALLY_SUPPORTED.
        #
        # We canonicalise once, here, so the stored answer, its citation rows, the
        # RAG Trace, Read Aloud and the transform validators all read one form.
        normalized_answer = normalize_citation_markers(result.answer)
        if normalized_answer != result.answer:
            logger.info(
                "Normalised non-ASCII citation brackets in the generated answer."
            )
            result.answer = normalized_answer

        # =================================================================
        # CITATION RESOLUTION
        # =================================================================
        with trace.stage("citation_resolution") as info:
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

        # =================================================================
        # GROUNDING
        # =================================================================
        with trace.stage("grounding") as info:
            grounding = check_grounding(
                result.answer,
                evidence_excerpts,
                citation_report,
                top_score=retrieval.top_score,
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

        # Annotate the trace once the run is complete. This has to happen HERE, not
        # earlier: `query_analysis` is recorded during retrieval, so writing to it
        # before the pipeline ran silently did nothing - the event did not exist yet
        # and the note was lost without any error.
        # Every query-understanding annotation is applied HERE, together.
        #
        # Writing them earlier silently did nothing: `query_analysis` is recorded during
        # retrieval, so before the pipeline runs there is no event to annotate. The
        # notes were computed correctly and then dropped without any error - which is
        # why the trace showed a resolved question as None while the answer was right.
        understanding_notes: dict[str, Any] = {}
        if followup_detected:
            understanding_notes.update(
                {
                    "original_question": question,
                    "resolved_question": resolved_question,
                    "is_followup": True,
                    "rewrite_reason": rewrite_note
                    or "The question needed the conversation; it was already "
                    "self-contained so the wording was left unchanged.",
                }
            )
        if unit_note:
            understanding_notes["unit_reference"] = unit_note

        if understanding_notes:
            for event in trace.events:
                if event.stage == "query_analysis":
                    event.data.update(understanding_notes)
                    break

        result.total_ms = int((time.perf_counter() - started) * 1000)
        return result


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
