"""
Hybrid retrieval: BM25 (lexical) fused with dense vector search via RRF.

WHY THIS FILE EXISTS
--------------------
Dense retrieval compares MEANING. That is what makes it good at "how does
virtualisation save money?" when the document never uses the word "money".

It is also what makes it weak at exact references. Ask for "RFC 1918", an error
code, an API identifier, or "UNIT III" and the embedding places it near every
other technical token in the same neighbourhood. The chunk that literally
contains the string is not necessarily the chunk that is semantically nearest.

BM25 answers a different question: which chunks contain the query's exact
terms, weighted by how rare those terms are? It is the oldest reliable answer
in information retrieval and it complements dense search in the one place dense
search is weak.

    dense   finds related meaning
    BM25    finds exact terms
    RRF     fuses the two orderings

WHAT "HYBRID" IS NOT
--------------------
It is not "BM25 score + cosine score". The two scales are unrelated: BM25 is
unbounded (0 to ~20+), cosine lives in [-1, 1]. Adding them weights whichever
happens to have the bigger numbers. Reciprocal Rank Fusion avoids the problem
entirely by using only each list's ORDER:

    RRF(d) = sum over lists of  1 / (k + rank_d)

A document first in either list scores ~1/(1+k); 50th in both lists still
scores less than 10th in one. Being high in ANY list is good, and being high in
BOTH is best. It needs no score normalisation, has one interpretable parameter
(k, conventionally 60), and is the standard method for exactly this problem.

CORRECTNESS GUARANTEES
----------------------
1. BM25 runs over the SAME population the vector search runs over - the
   workspace's chunks, with the identical document/page/section scope filters.
   If the two sides searched different populations, the fused ranking would
   be meaningless and the Retrieval Lab would show a comparison that could not
   happen in a real answer.

2. Fusion never invents evidence. A chunk enters the fused list only if it
   appeared in at least one of the two candidate lists. (RRF cannot surface a
   chunk neither method retrieved - it can only reconcile the two orderings.)

3. Scores are honest about what they are. Fused chunks carry the RRF score,
   and the dense-only score is kept in `original_score`. The trace labels the
   scale explicitly. No chunk claims a cosine similarity it does not have.

WHY BM25 IS COMPUTED PER QUERY, NOT INDEXED
------------------------------------------
A persisted inverted index is the production shape. It is not needed here and
it would add a second source of truth that can drift from SQLite. The corpus
per query is one workspace's chunk texts, fetched in the same transaction that
joins the vector candidates anyway. On the order of a few thousand chunks the
per-query cost is single-digit milliseconds - far less than one embedding
call - and the numbers the lab reports are always computed from what is
actually on disk right now, never from a stale index.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk
from app.rag.vectorstore import RetrievedChunk

# Tokenisation mirrors `retriever._query_terms` so the two lexical signals in
# the system (the blend and BM25) agree on what a word is.
_TOKEN = re.compile(r"[a-z0-9]+")

_HYBRID_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with",
    "using", "is", "are", "was", "were", "be", "what", "which", "who", "when",
    "where", "tell", "me", "about", "explain", "describe", "summarize",
    "summarise", "give", "show", "list", "does", "do", "did", "say", "says",
    "please", "can", "you", "your", "this", "that", "these", "those", "it",
    "its", "from", "at", "by", "as", "how", "why", "name",
}


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, stopwords removed.

    Stopwords are removed at index time so the IDF weighting is not diluted by
    them and the postings lists stay small. The same set is removed from the
    query, so term matching is consistent on both sides.
    """
    return [
        token
        for token in _TOKEN.findall((text or "").lower())
        if len(token) > 1 and token not in _HYBRID_STOPWORDS
    ]


@dataclass
class _Bm25Document:
    """One chunk in the BM25 corpus. Carries enough to rebuild a RetrievedChunk."""

    key: str                       # stable id: vector_id when present, else chunk:<id>
    tokens: list[str]              # full token list (term frequencies come from this)
    length: int                    # |tokens|, used for the length normalisation
    chunk: RetrievedChunk          # the candidate itself, returned on a hit


class Bm25Index:
    """Okapi BM25 over a transient corpus.

    Parameters are the classic defaults: k1=1.5 controls how much term frequency
    saturates, b=0.75 controls how strongly long documents are penalised. They
    are exposed so the lab can *demonstrate* their effect rather than hide it.
    """

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: list[_Bm25Document] = []
        self._doc_freq: dict[str, int] = {}
        self._avg_len: float = 0.0

    def __len__(self) -> int:
        return len(self._docs)

    @property
    def is_empty(self) -> bool:
        return not self._docs

    @classmethod
    def build(
        cls, candidates: Sequence[RetrievedChunk], *, k1: float = 1.5, b: float = 0.75
    ) -> "Bm25Index":
        """Index a candidate population.

        Each candidate is tokenised once; the per-document term frequency map is
        computed lazily during scoring so building stays linear in corpus size.
        """
        index = cls(k1=k1, b=b)
        total_length = 0

        for candidate in candidates:
            section = str((candidate.metadata or {}).get("section") or "")
            # The section title is indexed with the body. In lecture notes the
            # distinctive token is often ONLY in the heading ("UNIT III - CONCEPT
            # GENERATION"), and a query that names the unit should match on it.
            text = f"{section} {candidate.content}"
            tokens = tokenize(text)
            key = candidate.vector_id or f"chunk:{candidate.chunk_id}"
            index._docs.append(
                _Bm25Document(
                    key=key, tokens=tokens, length=len(tokens), chunk=candidate
                )
            )
            total_length += len(tokens)

            for term in set(tokens):
                index._doc_freq[term] = index._doc_freq.get(term, 0) + 1

        n = len(index._docs)
        index._avg_len = (total_length / n) if n else 0.0
        return index

    def _idf(self, term: str) -> float:
        """The Robertson-Sparck Jones IDF, in its standard non-negative form.

        Adding 1 to the numerator and denominator keeps the value >= 0 for every
        term (a term in every document gets idf -> 0, which is correct: it
        discriminates nothing). A negative idf would let a ubiquitous term
        *reduce* a document's score, which is a real trap in the naive formula.
        """
        n = len(self._docs)
        df = self._doc_freq.get(term, 0)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int) -> list[tuple[RetrievedChunk, float]]:
        """Score every document against the query. Returns (chunk, bm25 score).

        Scoring all documents is O(corpus) and deliberately so: it is the exact
        corpus for this query, it is small, and "compute everything and show it"
        is what keeps the lab honest. A skip-list / WAND optimisation would speed
        up large corpora but change nothing about the ranking.
        """
        if self.is_empty or top_k <= 0:
            return []

        query_terms = tokenize(query)
        if not query_terms:
            return []

        n_docs = len(self._docs)
        avg_len = self._avg_len or 1.0
        idf_by_term = {term: self._idf(term) for term in set(query_terms)}

        results: list[tuple[RetrievedChunk, float]] = []
        for document in self._docs:
            if document.length == 0:
                continue

            term_freq = Counter(document.tokens)
            score = 0.0
            for term in query_terms:
                frequency = term_freq.get(term, 0)
                if frequency == 0:
                    continue
                weight = idf_by_term[term]
                # BM25's saturation term: tf grows but flattens, so a chunk that
                # repeats a word 40 times is not 40x better than one that says it
                # once. The (k1 + 1) numerator keeps the maximum per-term score
                # bounded, which is the whole point versus raw tf.
                denominator = frequency + self.k1 * (
                    1.0 - self.b + self.b * (document.length / avg_len)
                )
                score += weight * (frequency * (self.k1 + 1.0)) / denominator

            if score > 0:
                results.append((document.chunk, round(score, 4)))

        results.sort(key=lambda pair: (-pair[1], pair[0].rank))
        return results[:top_k]


@dataclass
class HybridOutcome:
    """What one hybrid retrieval round produced, with both rankings preserved.

    Everything the Retrieval Lab and the trace show comes from this object: the
    two candidate lists exactly as each method ranked them, the fused list, the
    overlap, and which chunks each method found ALONE. The lab is comparing
    three orderings of the same corpus, so the comparison must be inspectable.
    """

    dense: list[RetrievedChunk] = field(default_factory=list)
    bm25: list[RetrievedChunk] = field(default_factory=list)
    fused: list[RetrievedChunk] = field(default_factory=list)
    rrf_k: int = 60
    dense_weight: float = 0.5
    bm25_ms: int = 0

    @property
    def overlap_ids(self) -> set[str]:
        dense_keys = {c.vector_id or f"chunk:{c.chunk_id}" for c in self.dense}
        bm25_keys = {c.vector_id or f"chunk:{c.chunk_id}" for c in self.bm25}
        return dense_keys & bm25_keys

    def as_dict(self) -> dict[str, Any]:
        # Each list reports the score on ITS OWN scale. The three lists are
        # heterogeneous: dense is cosine, bm25 is a BM25 number, and fused is the
        # reciprocal-rank hybrid value. Shoving all three into one `score` field
        # would erase that difference, and consumers already label the value by
        # the side they read (e.g. Learning Mode's "reciprocal-rank" tooltip), so
        # the wrong number under the right label is exactly the failure this
        # module's honesty notes exist to prevent.
        def shape(chunks: Sequence[RetrievedChunk], score_key: str) -> list[dict[str, Any]]:
            return [
                {
                    "vector_id": c.vector_id,
                    "chunk_id": c.chunk_id,
                    "document_name": c.document_name,
                    "label": c.citation_label(),
                    "section": str((c.metadata or {}).get("section") or ""),
                    "rank": c.rank,
                    "score": round(float(getattr(c, score_key, c.score)), 4),
                    "preview": (c.content or "")[:180],
                }
                for c in chunks
            ]

        dense_keys = {c.vector_id or f"chunk:{c.chunk_id}" for c in self.dense}
        bm25_keys = {c.vector_id or f"chunk:{c.chunk_id}" for c in self.bm25}

        return {
            "rrf_k": self.rrf_k,
            "dense_weight": self.dense_weight,
            "bm25_ms": self.bm25_ms,
            "dense_count": len(self.dense),
            "bm25_count": len(self.bm25),
            "fused_count": len(self.fused),
            "overlap_count": len(self.overlap_ids),
            "dense_only": len(dense_keys - bm25_keys),
            "bm25_only": len(bm25_keys - dense_keys),
            # `score` in each entry is that side's real score: cosine for dense,
            # bm25_score for the lexical side, rrf_score for the fused order.
            "dense": shape(self.dense, "score"),
            "bm25": shape(self.bm25, "bm25_score"),
            "fused": shape(self.fused, "rrf_score"),
        }


# ---------------------------------------------------------------------------
# Corpus construction from SQLite
# ---------------------------------------------------------------------------
def bm25_corpus_for_workspace(
    db: Session,
    workspace_id: int,
    *,
    document_ids: Sequence[int] | None = None,
    page_number: int | None = None,
    page_span: tuple[int, int] | None = None,
    section: str | None = None,
) -> list[RetrievedChunk]:
    """All indexed chunks for this query's population, as RetrievedChunks.

    The filters MUST match what `VectorStore.query` applies for the same
    request. If they did not, BM25 and dense would search different populations
    and the fused list would compare apples to oranges - the single most likely
    way to get "hybrid" subtly wrong. The same where-clause shape is used on
    both sides: workspace always, document id set when scoped, page and section
    metadata when filtered.
    """
    conditions = [Chunk.workspace_id == int(workspace_id)]
    if document_ids:
        conditions.append(Chunk.document_id.in_([int(d) for d in document_ids]))

    # page/section live inside the JSON metadata blob. They are applied in
    # Python after a cheap workspace+document fetch rather than via SQLite's
    # JSON1 operator, so the corpus BM25 searches is guaranteed to be the SAME
    # population the vector store returns for the same filters, and cannot
    # depend on an optional SQLite extension.
    rows = db.scalars(select(Chunk).where(*conditions)).all()

    corpus: list[RetrievedChunk] = []
    for row in rows:
        # Only chunks that actually exist in the vector index. A row with no
        # vector_id could never have appeared in the dense list, so including it
        # would let BM25 introduce evidence the vector search cannot back with a
        # similarity score - and the downstream join keys on vector_id, so such a
        # chunk would be dropped a moment later anyway. Equal populations keep
        # the fusion honest.
        if not row.vector_id:
            continue

        metadata = dict(row.doc_metadata or {})
        if page_number is not None:
            page = metadata.get("page_number")
            page_end = metadata.get("page_end") or page
            if page is None:
                continue
            if not (int(page) <= int(page_number) <= int(page_end)):
                continue
        elif page_span is not None:
            # Range coverage, the same test `VectorStore._scope_filter` applies:
            # a chunk overlaps [first, last] when it starts at or before `last`
            # and ends at or after `first`. Non-paginated chunks (page is None)
            # cannot overlap a page range and are excluded, exactly as the dense
            # filter's scalar-metadata comparison does.
            page = metadata.get("page_number")
            if page is None:
                continue
            page_end = metadata.get("page_end") or page
            first, last = int(page_span[0]), int(page_span[1])
            if not (int(page) <= last and int(page_end) >= first):
                continue
        if section is not None and str(metadata.get("section") or "") != section:
            continue

        corpus.append(
            RetrievedChunk(
                vector_id=row.vector_id or "",
                workspace_id=int(row.workspace_id),
                document_id=int(row.document_id),
                chunk_id=int(row.id),
                chunk_index=int(row.chunk_index),
                content=row.content,
                metadata=metadata,
                document_name=str(metadata.get("document_name") or ""),
                file_type=str(metadata.get("file_type") or ""),
                score=0.0,
                rank=-1,  # position in this list is meaningless for BM25
            )
        )
    return corpus


def reciprocal_rank_fusion(
    dense: Sequence[RetrievedChunk],
    sparse: Sequence[RetrievedChunk],
    *,
    rrf_k: int = 60,
    dense_weight: float = 0.5,
) -> list[RetrievedChunk]:
    """Fuse the two rankings into one by weighted reciprocal rank.

    Each list contributes `weight / (rrf_k + position + 1)` to a chunk's fused
    score. Only the ORDER of each list is used, so the result is scale-free: it
    never compares a cosine similarity to a BM25 number (they are unrelated
    scales). Being high in EITHER list is good; being high in BOTH is best.

    At the default dense_weight=0.5 the two weights are equal and this is exactly
    standard RRF. Setting it to 1.0 reduces the result to dense order alone, 0.0
    to BM25 order alone, which is what lets the Retrieval Lab demonstrate the
    fusion instead of hiding it behind a single number.

    `rrf_score` and `rrf_rank` are recorded on each returned chunk. `score` is
    left untouched (cosine, or 0.0 for a BM25-only chunk) so a downstream display
    never labels a reciprocal-rank value as a similarity.
    """
    sparse_weight = 1.0 - dense_weight
    contributions: dict[str, float] = defaultdict(float)
    best_rank: dict[str, int] = {}
    by_key: dict[str, RetrievedChunk] = {}

    for weight, ranking in ((dense_weight, dense), (sparse_weight, sparse)):
        if weight <= 0.0:
            continue
        for position, chunk in enumerate(ranking):
            key = chunk.vector_id or f"chunk:{chunk.chunk_id}"
            contributions[key] += weight / (rrf_k + position + 1)
            if key not in best_rank or position < best_rank[key]:
                best_rank[key] = position
            if key not in by_key:
                # Dense first, so a shared chunk keeps its richer provenance and
                # cosine score; BM25-only chunks carry score 0.0 honestly.
                by_key[key] = chunk

    fused = sorted(
        by_key.values(),
        key=lambda c: (
            -contributions[c.vector_id or f"chunk:{c.chunk_id}"],
            best_rank[c.vector_id or f"chunk:{c.chunk_id}"],
        ),
    )
    for position, chunk in enumerate(fused):
        key = chunk.vector_id or f"chunk:{chunk.chunk_id}"
        chunk.rrf_score = round(contributions[key], 6)
        chunk.rrf_rank = position
    return fused
