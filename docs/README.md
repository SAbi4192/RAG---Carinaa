# Carinaa — Documentation

**Every Answer, Traceable.**

Carinaa is an explainable RAG (Retrieval-Augmented Generation) document intelligence
platform. You upload documents, ask questions, and get answers that are **grounded in
your documents and cited to the exact place they came from**.

This documentation is written for a team that is **new to RAG**. Nothing is hidden
behind a framework. Every component is explained with the same six questions:

| Question | Why it matters |
| --- | --- |
| **What is it?** | The plain-English definition |
| **Why do we need it?** | The problem it solves — and what breaks without it |
| **How does it work?** | The actual mechanism, not a hand-wave |
| **Where does it fit?** | Which pipeline, which stage, which file |
| **What if we remove it?** | The honest consequence |
| **How do we demonstrate it?** | What to click, or what to run, to prove it works |

---

## Read in this order

| # | Document | What it covers |
| --- | --- | --- |
| 01 | [RAG Fundamentals](01_rag_fundamentals.md) | What RAG is, why it exists, the vocabulary |
| 02 | [Architecture](02_architecture.md) | The two pipelines, and where they meet |
| 03 | [Ingestion Pipeline](03_ingestion_pipeline.md) | Upload → parse → chunk → embed → store |
| 04 | [Embeddings & Vector Store](04_embeddings_and_vector_store.md) | Turning text into numbers, and searching them |
| 05 | [Retrieval & Query Pipeline](05_retrieval_and_query_pipeline.md) | Question → candidates → context → answer |
| 06 | [Advanced RAG](06_advanced_rag.md) | Re-ranking — and its hard limit |
| 07 | [Grounding](07_grounding.md) | Checking the answer against the evidence |
| 08 | [Citations & the Canonical Answer](08_citations_and_canonical_answer.md) | Provenance, and why there is one answer |
| 09 | [Offline & Online Modes](09_offline_and_online_modes.md) | Two providers, and the offline guarantee |
| 10 | [Security & Workspace Isolation](10_security_and_isolation.md) | The hard boundary, and prompt injection |
| 11 | [Database](11_database.md) | SQLite schema, and why it is the source of truth |
| 12 | [API Reference](12_api_reference.md) | Every endpoint, grouped by purpose |
| 13 | [Frontend Guide](13_frontend_guide.md) | The ten screens and what they show |
| 14 | [Evaluation](14_evaluation.md) | Measuring retrieval and generation honestly |
| 15 | [Demo Script](15_demo_script.md) | A step-by-step college presentation |

---

## Interactive diagrams

Self-contained HTML pages (no network, no build) — open them directly in a browser;
each has a theme toggle and a Print → Save as PDF button for slides.

| Diagram | What it shows |
| --- | --- |
| [**System Architecture & Live Workflow**](Carinaa-System-Workflow.html) | The whole system today: ingestion, hybrid retrieval (dense + BM25 → RRF), the streaming SSE protocol, per-message evidence, stage→file map, boundaries |
| [RAG Chat, Learning Mode & Pipeline Workflow](Carinaa-RAG-Workflow.html) | The conversational surfaces and the Learning panel, drawn per stage |
| [Architecture overview](Carinaa-Architecture.html) | The layered component view |

---

## Running it

Easiest: double-click **`Start-Carinaa.bat`** in the project root (see [README](../README.md)).
Or by hand:

```bash
# 1. Backend (from the project root)
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 --app-dir backend

# 2. Frontend, in a second terminal
cd frontend && npm run dev
```

Then open <http://localhost:5173>.

For a single-origin setup with no dev server, build the frontend once
(`cd frontend && npm run build`). The backend detects `frontend/dist` and serves it
at `/`, so <http://127.0.0.1:8000> becomes the whole application.

### Tests

```bash
# Backend test suite
cd backend && ../.venv/Scripts/python.exe -m pytest

# Include the test that loads the real 2 GB model
cd backend && ../.venv/Scripts/python.exe -m pytest -m slow

# Full end-to-end journey against a running server
.venv/Scripts/python.exe scripts/e2e_test.py --mode offline
```

### The two AI modes

| Mode | Provider | Needs internet? | Needs a key? |
| --- | --- | --- | --- |
| **Online** | Groq primary, Gemini fallback | Yes | Yes (`GROQ_API_KEY`, optional `GEMINI_API_KEY`) |
| **Offline** | Local GGUF via llama.cpp | **No** | **No** |

Offline mode is genuinely offline. It will never silently fall back to an online
provider — see [09](09_offline_and_online_modes.md) for why that is enforced by the
code's structure rather than by discipline.

---

## The one-paragraph summary

A document is uploaded and broken into **chunks**, each carrying its own provenance
(which page, which slide, which sheet and row range). Every chunk is converted into a
**vector** — a list of numbers representing its meaning — and stored in a local vector
database. When you ask a question, the question is converted into a vector the same
way, and the vector database returns the chunks whose meaning is closest. Those chunks
are assembled into a **context block** and given to a language model, which writes an
answer. Then — and this is the part most RAG demos skip — the answer is **checked
against the evidence** (grounding) and its **citations are resolved** to real chunks.
If the evidence does not support the answer, the system says so instead of guessing.
