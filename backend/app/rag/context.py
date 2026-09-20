"""
Context building.

WHY THIS IS A SEPARATE STAGE
----------------------------
"Retrieved" and "given to the model" are not the same thing.

Retrieval returns a ranked list of candidates with scores. Context building is the
editorial decision about which of those candidates actually go into the prompt, in
what order, under what numbers, and within what budget.

Getting this stage right matters for three reasons:

  1. BUDGET - an LLM has a finite context window. Ten 4,000-character chunks will
     blow past a small local model's 8k window and the answer will be truncated
     mid-sentence. We enforce a character budget and record what was dropped.

  2. NUMBERING - the `[1]` the model writes must be the same `[1]` the user clicks.
     Numbers are assigned HERE, once, in rank order, and that mapping is carried
     unchanged through generation, grounding and citation resolution.

  3. PROVENANCE - each excerpt carries the label shown to the model ("Cloud.pdf,
     p.32") so the model can refer to sources naturally, and the metadata needed to
     build the source card later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings


@dataclass
class Excerpt:
    """One numbered piece of evidence handed to the model."""

    number: int
    chunk_id: int | None
    vector_id: str
    document_id: int
    document_name: str
    file_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    rank: int = 0
    chunk_index: int | None = None
    # Score before re-ranking, so the UI can show the before/after comparison.
    original_score: float | None = None
    original_rank: int | None = None

    @property
    def label(self) -> str:
        parts = [self.document_name or f"Document {self.document_id}"]
        page = self.metadata.get("page_number")
        page_end = self.metadata.get("page_end")
        slide = self.metadata.get("slide_number")
        sheet = self.metadata.get("sheet_name")
        section = self.metadata.get("section")
        json_path = self.metadata.get("json_path")

        if page:
            parts.append(f"p.{page}-{page_end}" if page_end and page_end != page else f"p.{page}")
        elif slide:
            parts.append(f"slide {slide}")
        elif sheet:
            row_start, row_end = self.metadata.get("row_start"), self.metadata.get("row_end")
            if row_start and row_end and row_end != row_start:
                parts.append(f"{sheet}, rows {row_start}-{row_end}")
            elif row_start:
                parts.append(f"{sheet}, row {row_start}")
            else:
                parts.append(str(sheet))
        elif json_path:
            parts.append(str(json_path))

        if section:
            parts.append(str(section)[:80])
        return " | ".join(parts)

    def as_prompt_dict(self) -> dict[str, Any]:
        """The shape `build_context_block` consumes."""
        return {
            "number": self.number,
            "label": self.label,
            "content": self.content,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "chunk_id": self.chunk_id,
            "vector_id": self.vector_id,
            "document_id": self.document_id,
            "document_name": self.document_name,
            "file_type": self.file_type,
            "content": self.content,
            "metadata": self.metadata,
            "score": round(self.score, 4),
            "rank": self.rank,
            "chunk_index": self.chunk_index,
            "original_score": (
                round(self.original_score, 4) if self.original_score is not None else None
            ),
            "original_rank": self.original_rank,
            "label": self.label,
        }


@dataclass
class ContextBundle:
    """The assembled context plus a record of what it cost."""

    excerpts: list[Excerpt] = field(default_factory=list)
    characters: int = 0
    dropped: int = 0
    truncated: bool = False
    budget: int = 0
    notes: list[str] = field(default_factory=list)

    def as_prompt_list(self) -> list[dict[str, Any]]:
        return [e.as_prompt_dict() for e in self.excerpts]

    def as_dict(self) -> dict[str, Any]:
        return {
            "excerpt_count": len(self.excerpts),
            "characters": self.characters,
            "budget": self.budget,
            "dropped": self.dropped,
            "truncated": self.truncated,
            "notes": self.notes,
            "excerpts": [e.as_dict() for e in self.excerpts],
        }


def build_context(
    chunks: list[Any],
    *,
    max_chars: int | None = None,
    reserve_for_question: int = 900,
) -> ContextBundle:
    """Number, order and budget the retrieved chunks.

    `chunks` are `RetrievedChunk` objects (or anything with the same attributes).
    Ordering is by final score, descending - so excerpt [1] is always the strongest
    piece of evidence, which is both what the model should read first and what the
    user expects to see at the top of the source list.
    """
    budget = max_chars or settings.max_context_chars
    budget = max(500, budget - reserve_for_question)

    bundle = ContextBundle(budget=budget)

    if not chunks:
        bundle.notes.append("No chunks were retrieved, so the context is empty.")
        return bundle

    ordered = sorted(chunks, key=lambda c: getattr(c, "score", 0.0), reverse=True)

    used = 0
    number = 1

    for chunk in ordered:
        content = str(getattr(chunk, "content", "") or "").strip()
        if not content:
            continue

        # +40 accounts for the label line and separator in the rendered block.
        cost = len(content) + 40

        if used + cost > budget:
            if number == 1:
                # The very first excerpt does not fit. Truncating it is better than
                # sending an empty context, but we say so out loud.
                allowed = max(200, budget - 60)
                content = content[:allowed] + "\n[...excerpt truncated to fit the context budget]"
                cost = len(content) + 40
                bundle.truncated = True
                bundle.notes.append(
                    "The top-ranked excerpt was longer than the context budget and was "
                    "truncated."
                )
            else:
                bundle.dropped += 1
                bundle.truncated = True
                continue

        metadata = dict(getattr(chunk, "metadata", {}) or {})

        bundle.excerpts.append(
            Excerpt(
                number=number,
                chunk_id=getattr(chunk, "chunk_id", None),
                vector_id=str(getattr(chunk, "vector_id", "")),
                document_id=int(getattr(chunk, "document_id", 0) or 0),
                document_name=str(getattr(chunk, "document_name", "") or ""),
                file_type=str(getattr(chunk, "file_type", "") or ""),
                content=content,
                metadata=metadata,
                score=float(getattr(chunk, "score", 0.0) or 0.0),
                rank=int(getattr(chunk, "rank", 0) or 0),
                chunk_index=getattr(chunk, "chunk_index", None),
                original_score=getattr(chunk, "original_score", None),
                original_rank=getattr(chunk, "original_rank", None),
            )
        )
        used += cost
        number += 1

    bundle.characters = used

    if bundle.dropped:
        bundle.notes.append(
            f"{bundle.dropped} retrieved chunk(s) were dropped to stay within the "
            f"{budget}-character context budget."
        )

    return bundle


def context_stats(bundle: ContextBundle) -> dict[str, Any]:
    """Numbers for the Developer Mode panel. All measured, none estimated."""
    if not bundle.excerpts:
        return {
            "excerpts": 0,
            "characters": 0,
            "budget": bundle.budget,
            "utilization": 0.0,
            "dropped": bundle.dropped,
        }

    scores = [e.score for e in bundle.excerpts]
    return {
        "excerpts": len(bundle.excerpts),
        "characters": bundle.characters,
        "budget": bundle.budget,
        "utilization": round(bundle.characters / bundle.budget, 4) if bundle.budget else 0.0,
        "dropped": bundle.dropped,
        "truncated": bundle.truncated,
        "score_min": round(min(scores), 4),
        "score_max": round(max(scores), 4),
        "score_mean": round(sum(scores) / len(scores), 4),
        "documents_represented": len({e.document_id for e in bundle.excerpts}),
        "tokens_estimated": bundle.characters // 4,
    }
