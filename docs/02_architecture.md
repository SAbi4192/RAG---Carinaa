# 02 — Architecture

---

## The single most important design decision

Carinaa has **two pipelines** that never call each other:

```
┌──────────────────────────────────────────────────────────────────────┐
│  INGESTION PIPELINE                        runs once per document    │
│                                                                      │
│  Upload → Validate → Parse → Extract → Normalise → Chunk → Embed     │
│                                                              │       │
└──────────────────────────────────────────────────────────────┼───────┘
                                                               │
                                                    ┌──────────▼──────────┐
                                                    │                     │
                                                    │   THE VECTOR STORE  │
                                                    │                     │
                                                    └──────────▲──────────┘
                                                               │
┌──────────────────────────────────────────────────────────────┼───────┐
│  QUERY PIPELINE                            runs once per question    │
│                                                              │       │
│  Question → Analyse → Embed → Search → Retrieve → [Re-rank] →        │
│  Context → Generate → Ground → Citations → Answer                    │
└──────────────────────────────────────────────────────────────────────┘
```

The two halves share **exactly one thing**: the vector store. The ingestion pipeline
has no idea a question will ever be asked. The query pipeline has no idea how the
vectors got there.

### Why this separation matters

**It makes the cost profile honest.** Ingestion is slow and runs once. Query is fast
and runs constantly. If they were tangled together, every question would re-parse
documents — a 300-page PDF re-parsed on every keystroke.

**It makes failures local.** If a PDF parses badly, one document is wrong. The query
pipeline is untouched. If retrieval is slow, ingestion is unaffected.

**It makes the system testable.** You can test chunking without a language model. You
can test retrieval without a document. Each stage has a defined input and output.

**It is how real systems work.** This is not a simplification for a college project —
production RAG systems are built exactly this way, with ingestion as an offline job and
query as an online service.

### What if we merged them?

A "simple" single-function RAG looks like this:

```python
def answer(question, pdf_path):
    text = parse(pdf_path)          # slow, every time
    chunks = chunk(text)            # slow, every time
    vectors = embed(chunks)         # very slow, every time
    ...
```

It works in a demo with one small file. It collapses immediately in reality: the same
work repeated for every question, no way to add a document without re-processing all of
them, and no way to know which stage is slow.

---

## Component map

Where each piece of the architecture lives in the code.

### Ingestion

| Stage | File | Notes |
| --- | --- | --- |
| Upload / Validate | `backend/app/api/routes_documents.py` | Size, extension, and real content check |
| Parse | `backend/app/ingestion/parsers/` | One parser per format |
| Extract / Normalise | `backend/app/ingestion/normalizer.py` | All 8 formats → one internal shape |
| Types | `backend/app/ingestion/types.py` | The common internal representation |
| Chunk | `backend/app/ingestion/chunker.py` | Structure-aware, whole-block overlap |
| Embed | `backend/app/rag/embeddings.py` | fastembed / ONNX, local |
| Store | `backend/app/rag/vectorstore.py` + `backend/app/db/` | Vectors in Chroma, chunks in SQLite |
| Orchestration | `backend/app/ingestion/pipeline.py` | Runs the stages, reports real progress |

### Query

| Stage | File | Notes |
| --- | --- | --- |
| Orchestration | `backend/app/rag/pipeline.py` | The whole query flow |
| Query analysis | `backend/app/rag/pipeline.py` | Lightweight, rule-based |
| Embed question | `backend/app/rag/embeddings.py` | Same model as ingestion |
| Vector search | `backend/app/rag/vectorstore.py` | Chroma, hard-scoped to one workspace |
| Retrieve | `backend/app/rag/retriever.py` | Joins vectors back to database rows |
| Re-rank | `backend/app/rag/reranker.py` | Optional, off by default |
| Build context | `backend/app/rag/context.py` | Selects and formats excerpts |
| Prompts | `backend/app/rag/prompts.py` | System instruction + context block |
| Generate | `backend/app/llm/adapter.py` | Provider routing |
| Ground | `backend/app/rag/grounding.py` | Support check |
| Citations | `backend/app/rag/citations.py` | Resolve `[n]` to real evidence |
| Failsafe | `backend/app/rag/failsafe.py` | Honest answer when no model is available |
| Trace | `backend/app/rag/trace.py` | Records every stage as it really ran |

### Cross-cutting

| Concern | File |
| --- | --- |
| Configuration | `backend/app/core/config.py` |
| Errors | `backend/app/core/errors.py` |
| Logging + secret redaction | `backend/app/core/logging.py` |
| Auth, JWT, hashing | `backend/app/core/security.py` |
| Ownership dependencies | `backend/app/core/deps.py` |
| File cleanup | `backend/app/core/storage.py` |
| Database models | `backend/app/db/models.py` |
| Security self-tests | `backend/app/evaluation/security.py` |
| Metrics | `backend/app/evaluation/metrics.py` |

---

## The data that flows through

Understanding RAG is largely understanding **what shape the data is** at each step.

```
UPLOAD        bytes on disk
   ↓
PARSE         a tree of blocks, with structural position preserved
   ↓
NORMALISE     list[Block]  ── one common shape for all 8 formats
   ↓
CHUNK         list[Chunk]  ── text + provenance + a stable index
   ↓
EMBED         list[Chunk] + numpy array of shape (n_chunks, 384)
   ↓
STORE         vectors in Chroma; chunk rows in SQLite
   ══════════════════════════════════════════════════════════
RETRIEVE      list[RetrievedChunk] ── content + score + provenance
   ↓
CONTEXT       a formatted text block, numbered [1]..[n]
   ↓
GENERATE      a string, containing [n] markers
   ↓
GROUND        a verdict + per-claim support detail
   ↓
CITATIONS     [n] → real document, page, snippet
   ↓
ANSWER        text + citations + grounding + trace
```

The important transition is **NORMALISE**. Every format — PDF, DOCX, PPTX, XLSX, CSV,
Markdown, TXT, JSON — becomes the same `list[Block]`. Everything downstream is
format-blind. Adding a ninth format means writing one parser and nothing else.

The second important transition is at **STORE**, where the data forks permanently:
vectors go to Chroma (an *index*), and chunk records go to SQLite (the *source of
truth*). [11 — Database](11_database.md) explains why both exist.

---

## Provenance: the thread that never breaks

Every chunk carries where it came from, in whatever terms its format uses:

| Format | Provenance fields |
| --- | --- |
| PDF | `page_number`, `page_end` |
| DOCX | `section` (heading path) |
| PPTX | `slide_number`, `slide_end` |
| XLSX | `sheet_name`, `row_start`, `row_end` |
| CSV | `row_start`, `row_end` |
| Markdown | `section` |
| TXT | line range |
| JSON | `json_path` |

Plus, on every chunk: `document_id`, `chunk_id`, `chunk_index`, `block_type`.

This is what makes citations real. Without it, an answer could say "according to your
documents" and you would have no way to check. With it, the answer says
*"cloud_computing_notes.md · 2. Virtualization (score 0.7309)"* and you can go and read
that exact section.

**What if we dropped provenance?** Retrieval would still work. Answers would still be
generated. But citations would be impossible, grounding would have nothing to point at,
and the entire product would become an unverifiable chatbot. Provenance is not a
feature of Carinaa; it is the reason Carinaa exists.

---

## Real progress, never fake

A design rule that shows up throughout: **the UI must represent actual backend state.**

Ingestion reports progress by writing real stage transitions to the database, and the
frontend reads them. There is no timer that animates a bar to 100%. If parsing is stuck
on page 400 of a PDF, the UI says "Parsing" and stays there — because that is true.

The same rule applies to the RAG Trace: every stage shown is a real stage that ran,
with a real measured duration. Stages that did not run are marked "did not run" rather
than hidden.

**Why this matters for a demo.** A fake progress bar is a lie that the audience cannot
detect — until they upload a large file and watch it complete in three seconds. Real
progress is slower to build and impossible to catch out.

---

## Next

→ [03 — Ingestion Pipeline](03_ingestion_pipeline.md)
