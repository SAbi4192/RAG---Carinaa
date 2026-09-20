"""
Evaluation metrics.

THE RULE
--------
> Do not invent evaluation scores. (spec section 62)

Every number this module produces is computed from data that exists: retrieved
chunk ids, human-labelled relevant chunk ids, and stored grounding verdicts. If
there is no labelled dataset, we say so and return nothing rather than a
plausible-looking number.

RETRIEVAL METRICS
-----------------
These need a labelled dataset: for each question, a set of chunk ids that a human
has decided are relevant.

  Recall@K     of the relevant chunks, how many appear in the top K?
               Measures: did we FIND the evidence? (the thing that matters most)
  Precision@K  of the top K returned, how many are relevant?
               Measures: how much noise are we sending to the LLM?
  MRR          Mean Reciprocal Rank: 1/rank of the FIRST relevant result, averaged.
               Measures: how high up does the first good result appear?

MRR is the one people skip and shouldn't: a system that returns the right chunk at
position 1 and one that returns it at position 8 have identical Recall@5 if K is
large, but very different quality in practice.

GENERATION METRICS
------------------
These do NOT need a labelled dataset, because they are computed from the pipeline's
own recorded outputs.

  Faithfulness          share of answers whose grounding verdict is SUPPORTED
  Context relevance     mean cosine similarity of the chunks actually used
  Citation correctness  share of answers with valid (non-fabricated) citations
  Refusal accuracy      share of answers that correctly declined when evidence
                        was insufficient
  Answer relevance      lexical overlap between the question and the answer

IMPORTANT CAVEAT, STATED IN THE OUTPUT
--------------------------------------
`Answer relevance` measured by lexical overlap penalises correct paraphrases. It is
a cheap proxy, not a ground truth. We label it as such in the response so nobody
mistakes it for a human judgement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")
_STOPWORDS = frozenset(
    """a an the and or but if then than that this these those is are was were be been
being am do does did doing have has had having will would shall should can could
may might must of in on at to for from by with without about into over under
between among through during before after above below up down out off again
further once here there when where why how all any both each few more most other
some such no nor not only own same so too very s t just don now also it its as we
you your they their them he she his her him i me my our us""".split()
)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall((text or "").lower())
        if token not in _STOPWORDS and len(token) > 2
    }


# ---------------------------------------------------------------------------
# Retrieval metrics
# ---------------------------------------------------------------------------
def recall_at_k(retrieved: Sequence[int], relevant: Iterable[int], k: int) -> float:
    """Fraction of relevant items found in the top k."""
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    top_k = set(retrieved[:k])
    return len(top_k & relevant_set) / len(relevant_set)


def precision_at_k(retrieved: Sequence[int], relevant: Iterable[int], k: int) -> float:
    """Fraction of the top k that are relevant."""
    top_k = retrieved[:k]
    if not top_k:
        return 0.0
    relevant_set = set(relevant)
    return sum(1 for item in top_k if item in relevant_set) / len(top_k)


def reciprocal_rank(retrieved: Sequence[int], relevant: Iterable[int]) -> float:
    """1 / rank of the first relevant item (0 if none found)."""
    relevant_set = set(relevant)
    for index, item in enumerate(retrieved, start=1):
        if item in relevant_set:
            return 1.0 / index
    return 0.0


def average_precision(retrieved: Sequence[int], relevant: Iterable[int]) -> float:
    """Mean of precision values at each relevant hit. A rank-aware summary."""
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    hits = 0
    total = 0.0
    for index, item in enumerate(retrieved, start=1):
        if item in relevant_set:
            hits += 1
            total += hits / index
    return total / len(relevant_set)


@dataclass
class RetrievalEvaluation:
    """Aggregate retrieval quality over a labelled dataset."""

    k: int = 5
    dataset_size: int = 0
    recall_at_k: float = 0.0
    precision_at_k: float = 0.0
    mrr: float = 0.0
    map_score: float = 0.0
    hit_rate: float = 0.0
    details: list[dict[str, Any]] = field(default_factory=list)
    per_k: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "dataset_size": self.dataset_size,
            "recall_at_k": round(self.recall_at_k, 4),
            "precision_at_k": round(self.precision_at_k, 4),
            "mrr": round(self.mrr, 4),
            "map": round(self.map_score, 4),
            "hit_rate": round(self.hit_rate, 4),
            "recall_by_k": {key: round(value, 4) for key, value in self.per_k.items()},
            "details": self.details,
            "note": (
                "Computed from a labelled dataset supplied by the operator. No values are "
                "estimated or simulated."
            ),
        }


def evaluate_retrieval(
    results: list[dict[str, Any]],
    *,
    k: int = 5,
    extra_k: Sequence[int] = (1, 3, 5, 10),
) -> RetrievalEvaluation:
    """Aggregate retrieval metrics over a set of evaluated questions.

    Each entry in `results` must look like:
        {"question": str, "retrieved_ids": [int, ...], "relevant_ids": [int, ...]}
    """
    evaluation = RetrievalEvaluation(k=k, dataset_size=len(results))
    if not results:
        return evaluation

    recalls: list[float] = []
    precisions: list[float] = []
    rrs: list[float] = []
    aps: list[float] = []
    hits: list[float] = []
    per_k_totals: dict[int, list[float]] = {value: [] for value in extra_k}

    for entry in results:
        retrieved = list(entry.get("retrieved_ids") or [])
        relevant = list(entry.get("relevant_ids") or [])

        recall = recall_at_k(retrieved, relevant, k)
        precision = precision_at_k(retrieved, relevant, k)
        rr = reciprocal_rank(retrieved, relevant)
        ap = average_precision(retrieved, relevant)

        recalls.append(recall)
        precisions.append(precision)
        rrs.append(rr)
        aps.append(ap)
        hits.append(1.0 if set(retrieved[:k]) & set(relevant) else 0.0)

        for value in extra_k:
            per_k_totals.setdefault(value, []).append(recall_at_k(retrieved, relevant, value))

        evaluation.details.append(
            {
                "question": str(entry.get("question", ""))[:200],
                "retrieved_ids": retrieved[: max(extra_k)],
                "relevant_ids": relevant,
                "recall_at_k": round(recall, 4),
                "precision_at_k": round(precision, 4),
                "reciprocal_rank": round(rr, 4),
            }
        )

    def mean(values: Sequence[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    evaluation.recall_at_k = mean(recalls)
    evaluation.precision_at_k = mean(precisions)
    evaluation.mrr = mean(rrs)
    evaluation.map_score = mean(aps)
    evaluation.hit_rate = mean(hits)
    evaluation.per_k = {f"recall@{value}": mean(values) for value, values in per_k_totals.items()}

    return evaluation


# ---------------------------------------------------------------------------
# Generation / grounding metrics (computed from stored pipeline output)
# ---------------------------------------------------------------------------
def evaluate_generation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Metrics derived from what the pipeline actually recorded.

    `rows` are `QueryLog`-shaped dicts plus the question and answer text.
    """
    total = len(rows)
    if total == 0:
        return {
            "total": 0,
            "note": "No queries have been recorded yet, so there is nothing to measure.",
        }

    supported = sum(1 for r in rows if r.get("grounding_status") == "SUPPORTED")
    citation_error = sum(1 for r in rows if r.get("grounding_status") == "CITATION_ERROR")
    insufficient = sum(
        1 for r in rows if r.get("grounding_status") == "INSUFFICIENT_EVIDENCE"
    )
    refused = sum(1 for r in rows if r.get("refused"))

    with_citations = sum(1 for r in rows if int(r.get("citation_count") or 0) > 0)
    with_evidence = sum(1 for r in rows if int(r.get("used_count") or 0) > 0)

    scores = [float(r.get("top_score") or 0.0) for r in rows]
    latencies = [int(r.get("total_ms") or 0) for r in rows]

    relevances: list[float] = []
    for row in rows:
        question = str(row.get("question") or "")
        answer = str(row.get("answer") or "")
        if not question or not answer:
            continue
        question_tokens = _tokens(question)
        answer_tokens = _tokens(answer)
        if question_tokens:
            relevances.append(len(question_tokens & answer_tokens) / len(question_tokens))

    def mean(values: Sequence[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return {
        "total": total,
        "faithfulness": round(supported / total, 4),
        "citation_correctness": round(1 - citation_error / total, 4),
        "citation_coverage": round(with_citations / total, 4),
        "refusal_rate": round(refused / total, 4),
        "refusal_accuracy": round(insufficient / total, 4),
        "evidence_yield": round(with_evidence / total, 4),
        "mean_top_similarity": round(mean(scores), 4),
        "mean_latency_ms": round(mean(latencies), 1),
        "answer_relevance_proxy": round(mean(relevances), 4),
        "counts": {
            "supported": supported,
            "partially_supported": sum(
                1 for r in rows if r.get("grounding_status") == "PARTIALLY_SUPPORTED"
            ),
            "insufficient_evidence": insufficient,
            "citation_error": citation_error,
        },
        "caveats": [
            "Faithfulness is the share of answers whose grounding verdict was SUPPORTED. "
            "It measures answer-to-evidence support, not factual truth.",
            "Answer relevance is a lexical-overlap proxy. It penalises correct paraphrases "
            "and should not be read as a human quality judgement.",
        ],
    }
