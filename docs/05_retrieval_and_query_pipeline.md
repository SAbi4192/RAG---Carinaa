# 05 — Retrieval & Query Pipeline

*Question → Analyse → Embed → Vector Search → Retrieve → [Re-rank] → Build Context → Generate → Ground → Resolve Citations → Answer*

This pipeline runs **once per question** and must be fast.

---

## Stage 1 — Query analysis

**What is it?**
A lightweight, rule-based pass over the question before any model is involved.

**Why do we need it?**
Some questions should not reach the language model at all. "Hi" and "what can you do?"
are not questions about the documents. Sending them to a model wastes time and invites a
made-up answer.

**How does it work?**
Cheap heuristics: is it a greeting? is it empty after normalisation? is it a question
about the system rather than the documents? The result is recorded in the trace as a
real stage with a real duration.

**Where does it fit?**
`backend/app/rag/pipeline.py`

**What if we removed it?**
Every conversational turn costs a full retrieval + generation cycle, and greetings get
document-grounded answers, which reads as bizarre.

**How to demonstrate it**
Open the RAG Trace after asking "hello" and look at the `query_analysis` stage. It shows
what was decided and why.

---

## Stage 2 — Embed the question

**What is it?**
Converting the question into a vector using **the same model that embedded the chunks**.

**Why do we need it?**
A question and a chunk are only comparable if they live in the same vector space. This
is the most common beginner mistake in RAG, and it fails silently — see
[04](04_embeddings_and_vector_store.md).

**How does it work?**
`embed_query()` on the shared `EmbeddingService` singleton. Note the separate method
name: some models require different prefixes for queries and passages
(`embedding_query_prefix` / `embedding_passage_prefix` in config). Getting this wrong
degrades results subtly rather than breaking them.

**Where does it fit?**
`backend/app/rag/embeddings.py`

**What if we removed it?**
No vector search is possible.

**How to demonstrate it**
The RAG Trace shows the `query_embedding` stage with its dimension (384) and duration.

---

## Stage 3 — Vector search

**What is it?**
Asking the vector store for the chunks nearest to the question vector.

**Why do we need it?**
This is the retrieval in Retrieval-Augmented Generation. Everything before it is
preparation; everything after it depends on what it finds.

**How does it work?**
Chroma performs an HNSW search, hard-scoped to one workspace via a metadata `where`
clause. It returns the top `candidate_k` chunks (default 20) with their distances.

**Where does it fit?**
`backend/app/rag/vectorstore.py`

**What if we removed it?**
There is no RAG — only a language model answering from memory.

**How to demonstrate it**
Playground → retrieval-only. You see the real scored candidates with no model involved.

---

## Stage 4 — Candidate retrieval

**What is it?**
Joining the vector results back to the real chunk records in SQLite.

**Why do we need it?**
Chroma holds a vector and a *small scalar* metadata payload. The full chunk text, the
complete provenance, and the document name live in SQLite. Retrieval is where those two
halves are stitched back together.

**How does it work?**
For each returned vector id, load the corresponding `Chunk` row. Attach
`document_name`, `file_type` and the full metadata. Any vector whose chunk row is
missing is dropped (which happens if a delete raced with a query).

**Where does it fit?**
`backend/app/rag/retriever.py`

**What if we removed it?**
You would have scores and ids but no text to put in the prompt, and no page numbers to
cite.

**How to demonstrate it**
In the trace, compare the `vector_search` stage (ids and scores) with
`candidate_retrieval` (full records with document names and provenance).

---

## Stage 5 — Re-ranking (optional, off by default)

Covered in full in [06 — Advanced RAG](06_advanced_rag.md). The short version:

**It can only reorder the candidates that retrieval already found. It cannot recover
information that vector retrieval failed to retrieve.**

---

## Stage 6 — Build context

**What is it?**
Selecting which candidates actually go into the prompt, and formatting them.

**Why do we need it?**
Two separate jobs, both essential:

1. **Precision.** Retrieval returns `candidate_k` (20) candidates for *recall* — so the
   right one is somewhere in the list. But the model only needs the best `top_k` (5).
   Sending all 20 wastes context budget on noise and makes the model more likely to
   latch onto something irrelevant.
2. **Formatting.** The model needs to know which excerpt is which, so it can cite them.

**How does it work?**
Candidates are trimmed to `top_k`, then formatted into a numbered, delimited block:

```
=== BEGIN CONTEXT (untrusted document excerpts) ===

[1] cloud_computing_notes.md · 2. Virtualization
Virtualization allows multiple operating systems to run on a single physical
machine by abstracting the underlying hardware...

[2] cloud_computing_notes.md · 2.3 Hypervisors
A hypervisor is software that creates and manages virtual machines...

=== END CONTEXT ===
```

Total size is capped by `max_context_chars` (12,000). The delimiters and the
"untrusted" label are a security control, not decoration — see
[10](10_security_and_isolation.md).

**Where does it fit?**
`backend/app/rag/context.py`

**What if we removed it?**
The model would receive an undifferentiated wall of text with no way to cite a specific
part. Citations would become impossible.

---

### candidate_k vs top_k — the two-number idea

This trips people up, so it is worth stating plainly:

| Parameter | Default | Purpose |
| --- | --- | --- |
| `candidate_k` | 20 | **Recall.** Fetch generously, so the right chunk is somewhere in the list |
| `top_k` | 5 | **Precision.** Keep only the best, so the model sees signal not noise |

They are separate because they optimise opposite things. Increasing `candidate_k`
without re-ranking only helps if something later narrows the list — otherwise you are
just sending more noise to the model.

**How to demonstrate it**
Playground lets you change both values and watch the retrieved set change. Raise
`candidate_k` to 50 and `top_k` to 5 with re-ranking on: the extra candidates give the
re-ranker more to work with. With re-ranking off, they change nothing.

---

## Stage 7 — Generate

**What is it?**
Asking the language model to answer using the context block.

**Why do we need it?**
Retrieval finds evidence; it cannot write a sentence. The model's job is to read the
excerpts and compose an answer — grounded in them.

**How does it work?**
Two messages are sent to the LLM adapter:

- a **system message** containing the instructions (answer only from the context, cite
  with `[n]`, say so if the evidence is insufficient),
- a **user message** containing the question and the context block.

The pipeline calls `adapter.generate(...)` and does not know or care whether Gemini,
Groq or the local model answers.

**Where does it fit?**
`backend/app/rag/pipeline.py` → `backend/app/llm/adapter.py` → provider

**What if we removed it?**
You would have retrieval without an answer — useful for a search product, not for a
question-answering one. Carinaa still exposes this as the Playground's retrieval-only
mode.

**How to demonstrate it**
The provider badge on every answer names the provider that **actually** served it. If
Gemini failed and Groq answered, it says "Groq · Fallback". It never quietly shows the
primary's name for a fallback's work.

---

## Stage 8 — Grounding

**What is it?**
Checking the answer against the retrieved evidence.

**Why do we need it?**
Because a language model can write a fluent, confident sentence that the evidence does
not support. Grounding is the stage that catches it.

Full detail in [07 — Grounding](07_grounding.md).

---

## Stage 9 — Resolve citations

**What is it?**
Turning `[1]`, `[2]` in the answer into real document names, page numbers and snippets.

**Why do we need it?**
A citation the user cannot follow is worse than no citation, because it looks
verifiable and is not.

Full detail in [08 — Citations](08_citations_and_canonical_answer.md).

---

## The whole thing, with real numbers

A real trace from a question about a course-notes document:

| Stage | What happened | Duration |
| --- | --- | --- |
| `query_analysis` | Classified as a document question | ~1 ms |
| `query_embedding` | 384-dim vector produced | ~40 ms |
| `vector_search` | 6 candidates returned, top score 0.6078 | ~15 ms |
| `candidate_retrieval` | 5 full chunk records joined | ~8 ms |
| `reranking` | **skipped** — disabled | 0 ms |
| `context_building` | 5 excerpts → 4,684 chars | ~2 ms |
| `llm_generation` | Local model, 34 s | **34,561 ms** |
| `citation_resolution` | 1 citation resolved to a real chunk | ~1 ms |
| `grounding` | `PARTIALLY_SUPPORTED` | ~3 ms |

**Read that table again.** Every stage except generation takes milliseconds. Generation
takes 34 seconds on a CPU-only 3B model. This is the single most important performance
fact about the system: **the language model is the bottleneck, and everything else is
effectively free.**

It also explains why retrieval quality matters so much. Spending 40 ms to get retrieval
wrong costs you 34 seconds of generation producing a confident answer about the wrong
excerpt.

---

## Next

→ [06 — Advanced RAG](06_advanced_rag.md)
