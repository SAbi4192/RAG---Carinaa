# 06 — Advanced RAG

> Referenced from `backend/app/ingestion/chunker.py` and `backend/app/core/config.py`.

---

## The rule this document exists to enforce

> **Basic RAG must work before advanced RAG is attempted.**

Advanced techniques do not fix a broken foundation. They make a working system better.
If retrieval is returning the wrong chunks, semantic chunking and re-ranking will
produce better-ordered wrong chunks.

So Carinaa ships basic RAG working end to end, and treats the advanced techniques as
documented, measured, **optional** additions.

---

## Re-ranking

**What is it?**
A second, more expensive scoring pass over the candidates that vector search already
returned.

**Why do we need it?**
Vector search is fast because it is approximate. It compares a single vector
representation of the question against a single vector representation of each chunk.
That is a lossy comparison — it captures the general topic but misses fine distinctions.

A cross-encoder re-ranker reads the question and the chunk **together** and produces a
much more accurate relevance score. It is slower, so you cannot run it over 50,000
chunks. But over 20 candidates it is cheap.

The pattern is a funnel:

```
  50,000 chunks
        │  vector search (fast, approximate)   ← recall
        ▼
      20 candidates  (candidate_k)
        │  re-ranking (slow, accurate)          ← precision
        ▼
       5 excerpts    (top_k)
```

**How does it work?**
Carinaa uses a local cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2` via fastembed).
`rerank_enabled` defaults to **`False`**, so basic RAG is the default path.

**Where does it fit?**
`backend/app/rag/reranker.py`, called from `backend/app/rag/retriever.py`

---

### Turning it on (and what happens when you can't)

```bash
RERANK_ENABLED=true      # in .env
```

The cross-encoder is **not** downloaded with the project. On first use fastembed fetches
it (~80 MB) into `data/models/embeddings/`. Until then, `Reranker.available` is `False`.

**What happens if the model is missing — this is the part worth knowing:**

Re-ranking is an *optional refinement*. It is never allowed to break retrieval. When the
model cannot be loaded, `rerank()` returns the dense candidates **unchanged, in their
original order**, with:

```python
{"applied": False, "reason": "cross-encoder unavailable (...)", "duration_ms": 0}
```

The same holds if the model crashes mid-inference: the exception is caught, logged, and
the original ordering is returned. The RAG Trace then shows the `reranking` stage as
`skipped` with the reason — it does **not** pretend re-ranking ran.

This is deliberate. A feature that degrades visibly is fine; a feature that silently
returns worse results, or that turns a working retrieval into a 500, is not.

> **If you enable re-ranking and the first request takes ~2 minutes**, fastembed is
> retrying a failed download. Check the log for `Re-ranker unavailable:`. If a partial
> download is left behind (all files 0 bytes in
> `data/models/embeddings/models--Xenova--ms-marco-MiniLM-L-6-v2/`), delete that
> directory and try again — the cache is regenerable and gitignored.

### Two scores, two scales — never mix them

The cross-encoder score and the cosine similarity are **different scales and are not
comparable**. A cosine score of `0.73` and a re-rank score of `0.73` mean entirely
different things.

Carinaa therefore keeps them in separate fields and never combines them in a chart or a
sort:

| Field | Meaning |
| --- | --- |
| `original_score` / `cosine_score` | Dense vector similarity, from retrieval |
| `rerank_score` | Cross-encoder relevance, from re-ranking |
| `original_rank` / `rank` | Position before and after re-ranking |

Keeping `original_rank` is what lets the trace show **movement** — which chunk moved up,
which moved down, and how far. Without it you could only show the new order, not what
re-ranking actually did.

---

### ⚠ The hard limit — the most important sentence in this document

> **Re-ranking can only reorder the candidates that retrieval already found. It cannot
> recover information that vector retrieval failed to retrieve.**

If the chunk that answers the question was not in the top 20, re-ranking will never see
it. It cannot. Its entire input is those 20 chunks.

This means:

- **Re-ranking raises Precision@K.** Fewer irrelevant chunks reach the model.
- **Re-ranking cannot raise Recall@K.** Recall is fixed by vector search.
- **Re-ranking cannot fix a bad embedding model, bad chunking, or a missing document.**

This is why Carinaa measures both metrics separately
([14 — Evaluation](14_evaluation.md)) and why Recall@K is the one to watch. A high
Precision@K with a low Recall@K means "the system is confidently answering from an
incomplete candidate set" — which is exactly the failure mode that produces a
well-cited wrong answer.

**How to demonstrate it**

1. Playground → retrieval-only, re-ranking **off**. Note the top-5 and the top score.
2. Same query, re-ranking **on**. Note that the **same** chunks are present but in a
   different order, and the top score changes.
3. In the RAG Trace, look at the `reranking` stage: it reports how many candidates came
   in and how many went out. It never reports finding anything new.

Step 2 is the proof of the limit: the set of chunks is identical, only the order
changed.

---

## Why re-ranking is off by default

Three reasons, all deliberate:

1. **It adds a second model to load.** ~90 MB more, plus latency on every query.
2. **It can mask retrieval problems.** With re-ranking on, results look better, and a
   Recall@K problem becomes invisible. Turning it off first forces you to see the real
   retrieval quality.
3. **Basic RAG must be demonstrable on its own.** If the team cannot explain retrieval
   without re-ranking, they do not yet understand retrieval.

---

## Advanced techniques deliberately NOT implemented

Each of these is real and useful. Each was considered and deferred. The reasoning is
recorded so the decision is visible rather than forgotten.

### Semantic chunking

**What:** Split chunks where the *meaning* changes, using embeddings to detect topic
boundaries, instead of splitting on structure.

**Why deferred:** It hides the mechanism. With structure-aware chunking, you can always
say *why* chunk 7 starts where it does: there was a blank line, or a heading. With
semantic chunking, the boundary is wherever a model decided. For a teaching artefact,
explainability beats a marginal quality gain.

**Status:** Phase 7 experiment.

### GraphRAG / knowledge graphs

**What:** Build a graph of entities and relationships across documents, and traverse it
to answer questions that require connecting facts from several places.

**Why deferred:** It is a different system, not an increment. It needs entity
extraction, resolution, a graph store, and a completely different retrieval strategy. It
also solves a problem — multi-hop reasoning across documents — that the project's scope
does not have.

### Agentic / multi-hop RAG

**What:** Let the model decide to run several retrieval rounds, refining its own query
between them.

**Why deferred:** It multiplies latency and cost per question, and it makes the trace
non-deterministic in a way that is hard to explain in a demo. The current pipeline has a
fixed, inspectable shape — every stage in the trace always runs in the same order.

### Fine-tuning

**What:** Train the model on your documents.

**Why deferred:** Expensive, needs ML expertise, and — critically — a fine-tuned model
**still cannot cite a source**. It changes what the model knows, not whether its output
is verifiable. RAG and fine-tuning solve different problems; for a traceability product,
RAG is the right one.

### Hybrid search (BM25 + dense vectors)

**What:** Combine keyword search with vector search, so exact terms (part numbers, names,
rare identifiers) are not missed.

**Why deferred, but worth considering first:** This is the **most defensible next
addition**. Dense retrieval genuinely does miss exact-token matches, and hybrid search
attacks Recall@K — the metric that actually matters — rather than reordering an already-
complete candidate set. If the project continues, this is the recommended next step.

---

## If you are extending this

The order that respects the rule at the top of this document:

1. **Measure first.** Build a labelled evaluation set and get a real Recall@K baseline
   ([14](14_evaluation.md)). Without a number, you cannot tell whether a change helped.
2. **Fix recall before precision.** Hybrid search, better chunking, or a better embedding
   model. All of these raise the ceiling.
3. **Then add re-ranking.** It squeezes more out of a candidate set that is already
   complete.
4. **Only then** consider semantic chunking or multi-hop.

Adding them in the other order produces a system that is harder to explain and no more
correct.

---

## Next

→ [07 — Grounding](07_grounding.md)
