# 03 — Ingestion Pipeline

*Upload → Validate → Parse → Extract → Normalise → Chunk → Embed → Store → Ready*

This pipeline runs **once per document**. It is allowed to be slow. It reports real
progress the whole way.

---

## Stage 1 — Upload

**What is it?**
The HTTP endpoint that accepts a file and stores it on disk.

**Why do we need it?**
A file arrives as a stream of bytes with a filename attached. Before anything can
process it, it needs to exist somewhere stable with an identity we control.

**How does it work?**
`POST /api/workspaces/{id}/documents` accepts a multipart upload. The server:

1. Rejects files over `max_upload_mb` (default 50 MB) — before reading them fully.
2. Rejects extensions not in the supported list, with HTTP 415.
3. Stores the bytes under a **generated** filename (`uuid4().hex + ext`), never the
   user's original filename.
4. Creates a `Document` row with `status="pending"`.

**Where does it fit?**
`backend/app/api/routes_documents.py`

**What if we removed it?**
Nothing else can start. This is the entry point.

**Why a generated filename?**
The original filename is attacker-controlled. A file called `../../etc/passwd` or
`CON.pdf` on Windows is a path-traversal or device-name problem. Storing under a UUID
makes that class of bug impossible, and the original name is kept in the database for
display only.

**How to demonstrate it**
Upload a `.exe` renamed to `.pdf` — it is rejected at the *parse* stage, not here.
Upload a 200 MB file — rejected immediately with 413, without being read into memory.

---

## Stage 2 — Validate

**What is it?**
Confirming the file is genuinely what its extension claims.

**Why do we need it?**
Extensions are just text in a filename. A `.pdf` that is actually a ZIP archive will
crash a PDF parser with a confusing error, or worse, be silently mis-parsed.

**How does it work?**
Magic-byte sniffing — reading the first few bytes and comparing them to known
signatures (`%PDF-`, `PK\x03\x04` for Office formats, etc.).

**Where does it fit?**
`backend/app/ingestion/parsers/__init__.py` — each parser declares what it accepts.

**What if we removed it?**
Users get cryptic parser tracebacks instead of "this file does not appear to be a
valid PDF".

**How to demonstrate it**
Rename any text file to `.docx` and upload it. You get a clear parse failure recorded
on the document row, not a server error.

---

## Stage 3 — Parse

**What is it?**
Turning bytes into text **while preserving structure**.

**Why do we need it?**
Text alone is not enough. "42" on page 7 and "42" on page 200 are different facts. The
parser's job is to keep the *position* alongside the text.

**How does it work?**
One parser per format, each producing blocks with structural metadata:

| Format | Library | Structural position captured |
| --- | --- | --- |
| PDF | `pypdf` | page number |
| DOCX | `python-docx` | heading hierarchy |
| PPTX | `python-pptx` | slide number |
| XLSX | `openpyxl` | sheet name, row range |
| CSV | stdlib `csv` | row range |
| Markdown | custom | heading path |
| TXT | stdlib | line range |
| JSON | stdlib `json` | JSON path (`$.items[3].name`) |

**Where does it fit?**
`backend/app/ingestion/parsers/`

**What if we removed it?**
You could still read documents, but you could never cite them. "Somewhere in your
documents" is not a citation.

**Why eight separate parsers and not one clever one?**
Because a spreadsheet is not a document with pages, and pretending otherwise loses the
row numbers. Each format has a natural coordinate system; the parsers preserve each
one in its own terms, and normalisation unifies them afterwards.

**How to demonstrate it**
Upload the same content as `.csv` and as `.xlsx`. Both work, and both produce
citations that say "sheet, rows 12–14" and "rows 12–14" respectively — the natural
coordinates of each format.

---

## Stage 4 — Extract

**What is it?**
Deciding which parsed content is meaningful text worth indexing.

**Why do we need it?**
A PDF page contains text, but also running headers, footers, page numbers, and
sometimes watermarks. A spreadsheet contains data, but also empty cells and formatting.
Indexing noise wastes retrieval slots on chunks nobody will ever ask about.

**How does it work?**
Extraction keeps blocks that carry information and marks each with a `block_type`
(`paragraph`, `heading`, `table`, `code`, `list_item`, `record`). Repeated
page-furniture is filtered out.

**Where does it fit?**
Inside each parser, feeding `backend/app/ingestion/normalizer.py`.

**What if we removed it?**
Retrieval quality drops. Every chunk of boilerplate competes with real content for the
top-K slots.

**How to demonstrate it**
Open the Document Viewer and look at the extracted blocks. Headers and footers from the
PDF are absent; headings and tables are present and typed.

---

## Stage 5 — Normalise

**What is it?**
Converting eight different shapes into **one** common representation.

**Why do we need it?**
This is the architectural keystone. Everything downstream — chunking, embedding,
retrieval, citations — is format-blind. Without normalisation, every downstream stage
would need eight branches.

**How does it work?**
Every parser returns `list[Block]`, defined in `backend/app/ingestion/types.py`:

```python
@dataclass
class Block:
    text: str
    block_type: str          # paragraph | heading | table | code | list_item | record
    page_number: int | None
    slide_number: int | None
    sheet_name: str | None
    section: str | None      # heading path, e.g. "2. Virtualization > 2.3 Hypervisors"
    row_start: int | None
    row_end: int | None
    json_path: str | None
```

Format-specific fields are simply `None` where they do not apply. A DOCX block has
`section` set and `page_number` as `None`. A PDF block is the reverse.

**Where does it fit?**
`backend/app/ingestion/normalizer.py`, consuming `backend/app/ingestion/types.py`

**What if we removed it?**
Adding a ninth format would mean touching chunking, embedding, retrieval, context
building, citations and the UI. With it, adding a format means writing one parser.

**How to demonstrate it**
This is a good whiteboard moment for the demo. Show `list[Block]` from a PDF and from
an XLSX side by side: same type, different populated fields, and every downstream stage
treats them identically.

---

## Stage 6 — Chunk

**What is it?**
Splitting blocks into pieces of a size that retrieval can work with.

**Why do we need it?**
This is where retrieval quality is decided — more than anywhere else in the pipeline.

An embedding turns text into **one** vector. A vector is a single point, so it can only
represent one topic well:

- **Chunk too large** → the vector is a blurry average of several topics and matches
  nothing precisely.
- **Chunk too small** → no context to match against. A chunk reading "it increases by
  30%" matches nothing useful, because "it" is undefined.

Chunking is the search for the middle.

**How does it work?**
Four rules, in priority order:

1. **Structure-aware** — never split mid-paragraph if avoidable.
2. **Recursive** — if a block is too big, split on the largest natural boundary first:
   blank line → newline → sentence → word. Only fall back to a hard character split if
   none of those work (which happens with minified JSON or unbroken strings).
3. **Bounded** — target `chunk_size` (1000 chars), hard ceiling `max_chunk_chars`
   (4000), drop fragments under `min_chunk_chars` (80).
4. **Overlapped** — carry the tail of the previous chunk forward, so a fact that
   straddles a boundary is not lost from both chunks.

**Where does it fit?**
`backend/app/ingestion/chunker.py`

**What if we removed it?**
You would embed whole documents. One vector per document, averaging every topic in it.
Retrieval would return the entire document for every question, and the context window
would fill with irrelevant text.

---

### The overlap decision (worth understanding)

The naive overlap — "prepend the last 150 characters of the previous chunk" — breaks
provenance. Which page did those 150 characters come from? If you cannot answer, the
citation is a lie.

Carinaa overlaps by **whole blocks**. The tail blocks of the previous chunk are
re-included in full. Every character in every chunk therefore still maps to exactly one
source block, and therefore to exactly one page, slide, sheet or row.

The cost is a little extra text. The benefit is that **provenance stays exact**. For a
product whose entire value proposition is traceability, that trade is not close.

**How to demonstrate it**
The Learning Mode page has a live chunking lab. Paste text, move the size and overlap
sliders, and watch the chunks and their provenance update. It is the clearest way to
show that chunking is a real decision with real consequences.

---

### Why not semantic chunking?

Semantic chunking uses embeddings to find topic boundaries, so chunks split where the
subject changes. It is genuinely better in some cases.

We deliberately did not start with it:

- It is a Phase 7 experiment ([06 — Advanced RAG](06_advanced_rag.md)).
- It **hides the mechanism**. If chunk boundaries are decided by a model, a student
  cannot explain why chunk 7 starts where it does. Structure-aware chunking has a
  reason you can point at: there was a blank line there.

Basic RAG must work, and be *explainable*, before advanced RAG is attempted.

---

## Stage 7 — Embed

**What is it?**
Converting each chunk into a vector.

**Why do we need it?**
So that "which chunks are about this question?" becomes a geometry problem —
"which vectors are nearest to this one?" — which a computer can answer in milliseconds.

**How does it work?**
`fastembed` runs an ONNX model
(`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions) on the
local CPU. Chunks are embedded in batches of 32.

The model is loaded from a **local directory** (`data/models/fastembed`), not fetched
from the internet at query time, which is what makes offline mode possible.

**Where does it fit?**
`backend/app/rag/embeddings.py`

**What if we removed it?**
Retrieval would have to be keyword-based. Keyword search cannot match "how do I make my
app faster" to a chunk about "reducing latency" — no words in common, same meaning.
Embeddings are what make meaning-based search possible.

**How to demonstrate it**
Upload a document, ask a question using **completely different words** from the
document. It still finds the right chunk. That is the embedding doing its job.

See [04 — Embeddings & Vector Store](04_embeddings_and_vector_store.md) for the
detail, including the single most common beginner mistake (mixing models).

---

## Stage 8 — Store

**What is it?**
Writing the vectors to the vector store, and the chunk records to the database.

**Why do we need it?**
Two different things need storing, and they have different access patterns.

**How does it work?**
Both writes happen for each document:

- **Chroma** gets the vector, plus a small scalar metadata payload (workspace id,
  document id, page, sheet, row range, section, block type).
- **SQLite** gets the full chunk record: the complete text, the complete provenance,
  and the embedding model's name.

Chroma is an **index**. SQLite is the **source of truth**. See
[11 — Database](11_database.md).

**Where does it fit?**
`backend/app/rag/vectorstore.py` and `backend/app/db/`

**What if we removed it?**
Nothing would persist. Every restart would mean re-ingesting every document.

**How to demonstrate it**
Run `python -c "from app.rag.vectorstore import get_vector_store; print(get_vector_store().health())"`.
It prints the real vector count, straight from Chroma.

---

## Stage 9 — Ready

**What is it?**
Marking the document as searchable, and reporting real progress along the way.

**Why do we need it?**
A document that is half-ingested must not be retrievable, or a user could get an answer
citing a chunk that was never embedded.

**How does it work?**
Progress is written to the `Document` row as real stage transitions:
`pending → parsing → chunking → embedding → ready`, with a percentage derived from
actual work completed. The frontend reads this over Server-Sent Events.

If anything fails, the row goes to `status="failed"` with a real error message, and the
partial data is cleaned up.

**Where does it fit?**
`backend/app/ingestion/pipeline.py`

**What if we removed it?**
The UI would have to guess. Guessing means a fake progress bar — which is a lie the
audience can catch the moment they upload a large file.

**How to demonstrate it**
Upload a large PDF and watch the stage names change. Open a second browser tab and
watch it show the same stage — because both are reading the same database row, not
running their own animation.

---

## Next

→ [04 — Embeddings & Vector Store](04_embeddings_and_vector_store.md)
