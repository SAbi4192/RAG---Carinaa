# 04 — Embeddings & Vector Store

---

## Part 1 — Embeddings

**What is it?**
An embedding is a list of numbers that represents the *meaning* of a piece of text.
Carinaa uses 384 numbers per chunk.

**Why do we need it?**
Because keyword search cannot find what you mean.

Consider a document containing this sentence:

> Reducing latency in distributed systems requires careful attention to caching layers.

And this question:

> How do I make my app faster?

**No words in common.** Keyword search returns nothing. An embedding model places both
texts near each other in vector space, because they are about the same thing. That is
the entire reason embeddings exist.

**How does it work?**
The model reads text and outputs a vector. Training has arranged the space so that
similar meanings land in similar directions. "Cosine similarity" measures the angle
between two vectors: near 1 means "similar meaning", near 0 means "unrelated".

```
   "how do I make my app faster?"  ──┐
                                     ├──→  close together (≈0.73)
   "reducing latency ... caching"  ──┘

   "how do I make my app faster?"  ──┐
                                     ├──→  far apart (≈0.05)
   "the recipe calls for two eggs" ──┘
```

**Where does it fit?**
`backend/app/rag/embeddings.py`

**What if we removed it?**
Retrieval becomes keyword matching. It still works for exact terms (a part number, a
name) and fails for everything else. Roughly half of real questions would return nothing
useful.

---

### The model Carinaa uses

| Property | Value |
| --- | --- |
| Model | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| Dimensions | 384 |
| Runtime | `fastembed` (ONNX), CPU only |
| Size on disk | ~120 MB |
| Languages | Multilingual — relevant because Carinaa translates to Tamil, Malayalam, Telugu, Hindi, Japanese and Italian |

**Why local instead of an API?**

- **Offline mode is possible at all.** A cloud embedding API would make "offline" a
  lie.
- **Ingestion does not cost money per document.** Embedding a 300-page PDF is ~1,200
  embedding calls. At API prices that is a real cost per upload.
- **No rate limits.** Uploading ten documents in a row does not get throttled.
- **No data leaves the machine.** For a document intelligence product, this is a
  feature, not an implementation detail.

**Why ONNX and not PyTorch?**
PyTorch is roughly 2 GB of dependencies. `fastembed` uses ONNX Runtime, which is far
smaller and needs no GPU. For a project that must run on a student laptop, that
difference decides whether it runs at all.

---

### ⚠ The single most common beginner mistake

**Ingestion and query must use the same embedding model.**

If chunks were embedded with model A and the question with model B, the two vectors
live in *different coordinate systems*. Comparing them is meaningless.

What makes this dangerous is that **nothing crashes**. No exception, no warning. You
get retrieval results with plausible-looking scores that are pure noise, and an answer
built on irrelevant chunks.

This is why Carinaa:

- has **one** `EmbeddingService` singleton used by both pipelines,
- stores `embedding_model` on every document row and every evaluation run,
- can therefore tell you which model produced any given index.

**What if we removed that check?** You would eventually change the model in config,
re-index one document, and silently corrupt retrieval for that document — with no
symptom except slightly worse answers.

---

## Part 2 — The Vector Store

**What is it?**
A database that stores vectors and finds the nearest ones to a query vector.

**Why do we need it?**
Brute force is too slow. Comparing a question to 50,000 chunks means 50,000
calculations per question. A vector database keeps an **Approximate Nearest Neighbour**
index (HNSW) that finds the nearest vectors in milliseconds without checking all of
them.

It also stores a small metadata payload per vector, which is what makes **filtering**
possible — and filtering is what makes workspace isolation enforceable.

**How does it work?**
Carinaa uses **ChromaDB** in embedded mode:

- No server to run — it is a library, and it persists to a folder.
- HNSW index with `hnsw:space: cosine`, matching our L2-normalised embeddings.
- Metadata `where` filters, applied *inside* the index.
- Inspectable from Python in one line during a demo.

**Where does it fit?**
`backend/app/rag/vectorstore.py`

**What if we removed it?**
You would compare the question against every chunk on every query. For a single small
document this is fine. It stops being fine immediately, and the degradation is silent —
answers just get slower and slower.

---

### Why Chroma *and* SQLite?

This surprises people. Both store data. Why two?

Because they store **different things** and serve **different access patterns**:

| | ChromaDB | SQLite |
| --- | --- | --- |
| Role | **Index** | **Source of truth** |
| Holds | Vector + small scalar metadata | Full text, full provenance |
| Good at | "Find nearest vectors" | "Give me chunk 47's exact text and page number" |
| Can be rebuilt? | **Yes**, from SQLite | No — this is the original |

Chroma is explicitly *not* trusted as a record. It is a search accelerator. Consequences:

- **The index can be rebuilt.** Change the embedding model, or corrupt the index, and
  you can regenerate every vector from SQLite. Nothing is lost.
- **Retrieval can be audited.** You can read exactly which chunk was retrieved, long
  after the fact, because the chunk row still exists.
- **Deleting is clean.** A workspace delete is a SQL cascade plus one index delete.

**What if we removed SQLite?** Chroma would become the only copy. Chroma's metadata
values must be scalars, so full text and rich provenance do not fit comfortably. A
model change would mean re-parsing every document from the original files — which may
no longer exist.

---

### The workspace filter is not optional

Every single query in `vectorstore.py` goes through `_scope_filter`, which **always**
includes `workspace_id`. There is no code path that searches across workspaces.

On top of that, `query()` re-checks the workspace on every returned row before handing
it back. If a row's workspace does not match, it is dropped and an error is logged.

**Two independent protections, because they fail differently:**

1. The `where` filter means foreign vectors are never even scored.
2. The post-retrieval check means a bug or a Chroma behaviour change cannot silently
   leak data.

This is defence in depth, and it is tested directly —
see [10 — Security](10_security_and_isolation.md) and
`backend/tests/test_workspace_isolation.py`.

---

### Reading a result: score, not distance

Chroma returns a **distance** (lower is closer). Carinaa converts it to a
**similarity score** (higher is closer):

```python
score = 1.0 - distance
```

The UI shows scores like `0.7309`. The clamp to `[-1.0, 1.0]` exists so that a tiny
floating-point overshoot never displays as `1.0000000002`.

**How to read a score:** above ~0.7 is a strong match, 0.5–0.7 is usually relevant,
below ~0.4 is often noise. There is no universal threshold — it depends on the corpus
and the model. This is why Carinaa's `min_relevance_score` defaults to `0` (disabled):
a bad threshold silently removes good evidence, which is worse than sending slightly
noisy context to the model.

---

## How to demonstrate both

```bash
# See the real index, straight from Chroma
cd backend
../.venv/Scripts/python.exe -c "
from app.rag.vectorstore import get_vector_store
print(get_vector_store().health())
"
```

Then in the UI:

1. **Playground** → run a retrieval-only query. You see real scored candidates with
   their provenance, before any language model is involved.
2. **Document Viewer** → inspect the actual chunks that were stored, with their
   provenance.
3. **Chat** → ask a question whose words do **not** appear in the document. It still
   finds the right chunk. That is the embedding at work.

---

## Next

→ [05 — Retrieval & Query Pipeline](05_retrieval_and_query_pipeline.md)
