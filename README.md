# Carinaa

**Every Answer, Traceable.**

An explainable, educational, secure **RAG (Retrieval-Augmented Generation)** document
intelligence platform. Upload documents, ask questions, and get answers that are grounded
in your documents and cited to the exact place they came from.

Built as a college project with one rule: **nothing is hidden behind a framework.** Every
component is documented with What / Why / How / Where / What-if-removed / How-to-demonstrate.

---

## It is a conversation, not a search box

Carinaa remembers the chat and understands what a question is pointing at.

- **Conversation memory.** "My name is Abishek" then, several messages later, "What is
  my name?" answers correctly. Retrieval uses a rewritten standalone question;
  generation uses your original words plus the history, kept in a separate block
  labelled as conversation rather than document evidence - so a previous answer is
  never cited as a source.
- **Page references.** "Tell me about page 2", "the second page", "p.12" are detected
  and applied as a metadata filter inside the vector index, not hoped for in the
  ranking. A page that does not exist, a file with no pages, and an ambiguous document
  each get their own accurate answer.
- **Relative references.** "What about the next page?" resolves from the page the
  conversation is already on.
- **Section references.** "Tell me about the Interviewing Techniques section" matches
  against the section titles actually indexed.
- **Unit references.** "the third unit", "Unit III", "unit 3" and "unit five" all
  resolve to the same thing. An embedding cannot bridge an ordinal to a Roman numeral —
  that is a transformation, not a similarity — so the canonical form is added to the
  search. This also works when the document's headings skip a unit, which filtering
  would not.

---

## Learn by watching it work

The system is also a **RAG learning platform**. You do not have to take anyone's word for
how it works — you can watch it, one stage at a time, on your own documents.

- **Learning Mode** (a toggle in Chat) shows the real pipeline beneath every answer.
- **The RAG Laboratory** (`/app/playground`) has ten benches. Seven follow the stages of
  one question — chunking, embeddings, vector store, retrieval, context, generation and
  the full pipeline. Three more are about what a conversation adds on top: the
  **Reference Lab** (why "page 2" must be a filter, not a search term), the **Scope Lab**
  (how "answer only from this document" is enforced) and the **Memory Lab** (why the
  question you type is not the question that gets searched). Each runs the same code the
  real pipeline runs.
- **The Full Pipeline bench** pauses, steps forward and steps back, for demonstrating at a
  lectern.

Every bench follows one rule: **if a stage is not enabled in the current configuration, it
says so and shows nothing in its place.** A teaching tool that animates plausible-looking
activity is worse than no teaching tool, because you cannot tell the animation from the
system.

---

## What makes it different

| | Typical "chat with your PDF" | Carinaa |
| --- | --- | --- |
| Citations | "According to your documents" | `[1] cloud_computing_notes.md · 2. Virtualization (0.7309)` |
| Grounding | Not checked | Four verdicts, including **`INSUFFICIENT_EVIDENCE`** |
| Refusal | Invents an answer | Says "the documents do not cover this" |
| Offline | Requires an API | **Runs with no network at all** — and proves it with tests |
| Retrieval | Hidden | Inspectable in the Playground, with real scores |
| Pipeline | A black box | Nine traceable stages with measured durations |

---

## Quick start

Run everything from the **project root** (`RAG - Carinaa/`).

### 1. Backend (this also serves the frontend)

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 --app-dir backend
```

Then open **http://127.0.0.1:8000** — one origin, no CORS, no second server.

If `frontend/dist/` does not exist yet, build it once (step 2), then start the backend.

### 2. Frontend

The build is already committed as `frontend/dist/`. To rebuild after changing UI code:

```bash
cd frontend
node node_modules/typescript/bin/tsc --noEmit      # typecheck
node node_modules/vite/bin/vite.js build           # then reload http://127.0.0.1:8000
```

> **Note:** use `node node_modules/...` rather than `npm run build` on this machine. npm's
> shim resolves against the wrong Node and fails with `MODULE_NOT_FOUND` even though the
> tools are installed. `npm install` has already been run.

### 3. Live-reload development (optional)

```bash
cd frontend && node node_modules/vite/bin/vite.js     # http://localhost:5173
```

Vite proxies `/api` to the backend on port 8000, so run both.

### First run

Open the app, sign up, create a workspace, upload `samples/cloud_computing_notes.md`, and
ask: *"How does virtualization improve resource utilization?"*

### AI modes

| Mode | Provider | Internet | API key |
| --- | --- | --- | --- |
| **Online** | Gemini → Groq fallback | Required | `GEMINI_API_KEY` |
| **Offline** | Local GGUF (llama.cpp) | **Not required** | **Not required** |

`.env` is already configured with both keys. Offline mode needs nothing — it uses
`models/llm-model.gguf` (2.0 GB), which is already on disk. The mode is chosen **per
question** in the Playground; it is not a global setting.

---

## Tests

```bash
# Backend test suite — 121 fast tests
cd backend && ../.venv/Scripts/python.exe -m pytest -m "not slow"

# Include the tests that load the real 2 GB model
cd backend && ../.venv/Scripts/python.exe -m pytest -m slow

# Full end-to-end journey against a running server
.venv/Scripts/python.exe scripts/e2e_test.py --mode online
.venv/Scripts/python.exe scripts/e2e_test.py --mode offline

# Every RAG Laboratory bench has working data behind it
.venv/Scripts/python.exe scripts/verify_labs.py --mode online

# Real-browser UI test: console errors, responsive widths, click-through
.venv/Scripts/python.exe scripts/ui_test.py

# Generate a small multi-page PDF for page-aware retrieval testing
.venv/Scripts/python.exe scripts/make_test_pdf.py
.venv/Scripts/python.exe scripts/ui_test.py --headed   # watch it run

# Isolate one feature (translation + citation preservation)
.venv/Scripts/python.exe scripts/probe_translate.py --mode online --language ta

# Does the frontend still match the backend's API and stage order?
.venv/Scripts/python.exe scripts/check_type_drift.py

# Frontend typecheck + production build
cd frontend && node node_modules/typescript/bin/tsc --noEmit
cd frontend && node node_modules/vite/bin/vite.js build
```

**Current status: 216 pytest tests passing · E2E 44/44 · 23/23 laboratory checks ·
45/45 browser UI checks with 0 console errors · 6/6 security self-tests · 0 type drift.**

The browser test drives the real Edge already on the machine (`channel="msedge"`), so it
needs no Playwright browser download. It checks every route for console errors and failed
requests, exercises the chat and the panel controls, and verifies there is no horizontal
overflow at 1440 / 1280 / 1024 / 768 / 390 px.

The E2E script registers a throwaway account, so it never touches your data.

---

## Architecture

Two pipelines that meet in exactly one place.

```
INGESTION   Upload → Validate → Parse → Extract → Normalise → Chunk → Embed → Store → Ready
                                                          │
                                              ┌───────────▼───────────┐
                                              │    THE VECTOR STORE   │
                                              └───────────▲───────────┘
                                                          │
QUERY       Question → Analyse → Embed → Search → Retrieve → [Re-rank] →
            Context → Generate → Ground → Citations → Answer
```

The ingestion pipeline has no idea a question will ever be asked. The query pipeline has
no idea how the vectors got there. This separation is what makes ingestion slow-but-once
and querying fast-and-constant.

**Stack:** FastAPI · SQLite (source of truth) · ChromaDB (vector index) · fastembed/ONNX
(local embeddings) · llama.cpp + GGUF (offline generation) · React + Vite + Tailwind.

---

## Documentation

Start at **[docs/README.md](docs/README.md)**.

| # | Document |
| --- | --- |
| 01 | [RAG Fundamentals](docs/01_rag_fundamentals.md) — start here if RAG is new |
| 02 | [Architecture](docs/02_architecture.md) |
| 03 | [Ingestion Pipeline](docs/03_ingestion_pipeline.md) |
| 04 | [Embeddings & Vector Store](docs/04_embeddings_and_vector_store.md) |
| 05 | [Retrieval & Query Pipeline](docs/05_retrieval_and_query_pipeline.md) |
| 06 | [Advanced RAG](docs/06_advanced_rag.md) — re-ranking and its hard limit |
| 07 | [Grounding](docs/07_grounding.md) |
| 08 | [Citations & the Canonical Answer](docs/08_citations_and_canonical_answer.md) |
| 09 | [Offline & Online Modes](docs/09_offline_and_online_modes.md) |
| 10 | [Security & Workspace Isolation](docs/10_security_and_isolation.md) |
| 11 | [Database](docs/11_database.md) |
| 12 | [API Reference](docs/12_api_reference.md) |
| 13 | [Frontend Guide](docs/13_frontend_guide.md) |
| 14 | [Evaluation](docs/14_evaluation.md) |
| 15 | [Demo Script](docs/15_demo_script.md) — for the presentation |

---

## Honest limits

Stated here because they are part of the design, not footnotes:

- **Grounding does not guarantee correctness.** It measures the relationship between an
  answer and its evidence. An answer can be perfectly grounded in a document that is
  itself wrong.
- **Re-ranking cannot fix retrieval.** It only reorders candidates that were already
  found. If the right chunk was not retrieved, no downstream stage can recover it.
- **Prompt injection cannot be eliminated.** It is mitigated — documents are framed as
  untrusted data, and the prompt never contains a secret — but mitigation is not
  elimination.
- **No accuracy percentage is reported.** Measuring it requires labelled data. Inventing a
  number would be worse than showing none.
- **Advanced RAG is deliberately deferred.** Basic RAG must work, and be explainable,
  before semantic chunking, GraphRAG or multi-hop retrieval are attempted.

---

## Project layout

```
backend/
  app/
    api/          HTTP routes
    core/         config, errors, logging, security, deps, storage
    db/           SQLAlchemy models and session
    ingestion/    parsers (8 formats), normalizer, chunker, pipeline
    rag/          embeddings, vectorstore, retriever, reranker, context,
                  prompts, citations, grounding, failsafe, trace, pipeline
    llm/          adapter, gemini, groq, local
    features/     translate, shorten, speech, explain
    evaluation/   metrics, security self-tests
  tests/          pytest suite
frontend/
  src/
    lib/          types, api client, formatting, speech
    state/        theme, auth, workspace, toast
    components/   ui primitives, layout, chat, documents, trace
    pages/        the ten screens
docs/             this documentation set
scripts/          e2e_test.py, fetch_models.py, inspect_gguf.py, repro_delete.py
samples/          a sample document to demo with
```
