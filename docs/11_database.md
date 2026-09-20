# 11 — Database

> Referenced from `backend/app/db/session.py`.

---

## Why a relational database at all

A reasonable question: if the vector store holds the chunks, why also keep SQLite?

Because **Chroma is an index, not a record.** It stores a vector and a small payload of
scalar metadata. It cannot comfortably hold full chunk text or rich provenance, and it is
not designed to be your system of record.

SQLite is the **source of truth**. Chroma is a search accelerator that can be rebuilt
from it.

| | SQLite | ChromaDB |
| --- | --- | --- |
| Role | Source of truth | Index |
| Holds | Full text, full provenance, every relationship | Vector + scalar metadata |
| Rebuildable? | No — this is the original | **Yes, from SQLite** |
| Query style | SQL, joins, transactions | Nearest-neighbour search |
| Used for | Everything authoritative | Retrieval only |

**Consequences of this split:**

- **The index can be rebuilt.** Change the embedding model, or recover from index
  corruption, and regenerate every vector from SQLite. Nothing is lost.
- **Retrieval can be audited.** The chunk that was retrieved still exists as a row, long
  after the fact.
- **Deletes are clean.** A workspace delete is a SQL cascade plus one index delete.

**What if we dropped SQLite and used Chroma alone?** The index becomes the only copy. A
model change would mean re-parsing every original file — which may no longer exist. And
you would lose the ability to answer "what exactly was retrieved for that question three
weeks ago?"

---

## Why `create_all` and not Alembic

The project is a teaching artefact. A student should be able to delete
`data/carinaa.db`, restart, and get a clean working database.

Alembic is the right tool for a production schema that must evolve in place without data
loss. It is also a second concept to learn before the first one is understood.

**The trade-off, stated honestly:** if the schema changes while real data exists, you must
either migrate by hand or start fresh. For this project, starting fresh is acceptable —
documents can be re-ingested. For a production system it would not be, and Alembic would
be the first thing to add.

---

## The schema

Ten tables. The relationships are the important part.

```
User
 ├── Workspace  (many)
 │    ├── Document  (many)
 │    │    └── Chunk  (many)          ← the retrievable unit
 │    ├── Conversation  (many)
 │    │    └── Message  (many)        ← one turn; assistant turns hold the answer
 │    │         ├── AnswerVariant     ← translations, shortenings
 │    │         └── TraceEvent        ← one real stage of one query
 │    └── EvaluationRun
 └── QueryLog
```

### `users`

Email, bcrypt password hash, display name, and a JSON `preferences` blob (theme,
language, default AI mode, default top_k, learning mode, developer mode).

### `workspaces`

The **isolation boundary**. Every workspace belongs to exactly one user. Every document,
conversation and chunk traces back to a workspace.

### `documents`

One uploaded file. Notable columns:

- `stored_filename` — the generated UUID name on disk
- `original_filename` — what the user called it, for display
- `status` — `pending | parsing | chunking | embedding | ready | failed`
- `stage` — the current stage, so progress is readable
- `error` — the real failure message when status is `failed`
- `embedding_model` — **which model produced this document's vectors**

That last column is what makes the "same model for ingestion and query" rule enforceable
rather than aspirational ([04](04_embeddings_and_vector_store.md)).

### `chunks`

The retrievable unit, and the most important table in the system.

Holds the full chunk text plus complete provenance: `document_id`, `workspace_id`,
`chunk_index`, `page_number`, `page_end`, `slide_number`, `sheet_name`, `section`,
`row_start`, `row_end`, `json_path`, `block_type`.

**This is why citations work.** A citation is a `chunk_id` joined to a `document_id`
joined to a `document_name` and a page number. Remove this table and the product loses
its reason to exist.

### `conversations` and `messages`

`conversations` belongs to a workspace and a user — this is the ownership path used by
the trace-scan fix ([10](10_security_and_isolation.md)).

`messages.content` is the **canonical grounded answer** and is immutable
([08](08_citations_and_canonical_answer.md)). Assistant messages also carry the real
provenance of the answer: `ai_mode`, `provider`, `model`, `used_fallback`,
`grounding_status`, `latency_ms`, `token_usage`, `retrieval`, `citations`.

### `answer_variants`

One row per presentation transform: `kind` (`translated | shortened | explanation`),
`language`, `level`, and the content. A unique constraint on
`(message_id, kind, language, level)` means a cached variant is never duplicated.

### `trace_events`

One real stage of one query. Powers the RAG Trace page.

**Security note, enforced by convention:** only measured facts are stored here — stage,
status, duration, counts, scores, model. Never an API key, never a hidden prompt, never
private chain-of-thought. The self-test suite walks every row and asserts this.

### `query_logs`

One aggregate row per query, for the Analytics page. Real numbers only — counts, scores,
durations. This is what makes `p50 latency` and `faithfulness` computable without
re-reading every message.

### `evaluation_runs`

A stored retrieval evaluation: the labelled dataset size, the computed metrics, and the
`embedding_model` and `rerank_enabled` flags the run used. Storing those two flags is
what makes two runs comparable — otherwise you cannot tell whether a metric moved because
of a real change or because the configuration differed.

---

## SQLite configuration

Two pragmas matter, and both are set on every connection:

```sql
PRAGMA journal_mode=WAL;    -- readers do not block the writer
PRAGMA foreign_keys=ON;     -- OFF by default in SQLite
```

**WAL (write-ahead logging).** The ingestion worker writes while a chat request reads.
Without WAL, one blocks the other, and "upload a document, then immediately ask about it"
would stall.

**Foreign keys.** SQLite disables them by default. Without this pragma, cascade deletes
silently do nothing — and a deleted workspace would leave orphaned chunks behind. Those
orphans would still be in the vector index, and could still be retrieved. This is a
one-line setting standing between you and a genuinely confusing data-integrity bug.

`busy_timeout` is also set to 30 s, so a brief lock contention waits rather than erroring.

---

## Inspecting the database

```bash
cd backend
../.venv/Scripts/python.exe -c "
import sqlite3
conn = sqlite3.connect('../data/carinaa.db')
for (table,) in conn.execute(
    \"select name from sqlite_master where type='table' order by name\"
):
    count = conn.execute(f'select count(*) from {table}').fetchone()[0]
    print(f'{table:20} {count:>6}')
"
```

Useful queries for a demo:

```sql
-- What was retrieved for a given answer?
select retrieval from messages where id = 42;

-- Every stage of a query, in order
select seq, stage, status, duration_ms from trace_events
where message_id = 42 order by seq;

-- Are any documents stuck mid-ingestion?
select id, original_filename, status, stage from documents where status != 'ready';

-- Which embedding model produced each document's vectors?
select id, original_filename, embedding_model from documents;
```

---

## Next

→ [12 — API Reference](12_api_reference.md)
