# 12 — API Reference

All endpoints are under `/api`. Interactive docs are served at **`/docs`** (Swagger) and
**`/redoc`**.

**Authentication:** every endpoint except `/api/health` and `/api/auth/register|login`
requires `Authorization: Bearer <token>`.

**Error shape:** all errors use the same envelope.

```json
{
  "error": {
    "code": "not_found",
    "message": "That workspace does not exist.",
    "detail": {}
  }
}
```

| Status | Meaning in Carinaa |
| --- | --- |
| 401 | Missing or invalid token |
| 404 | Not found **or not yours** — deliberately indistinguishable ([10](10_security_and_isolation.md)) |
| 415 | Unsupported file type |
| 413 | File too large |
| 422 | Validation failed, or a transform was rejected |
| 503 | A required provider or model is unavailable |

---

## Health

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/health` | Fast liveness check. No auth. |
| `GET` | `/api/health/deep` | Checks database, vector store, embedding model, providers |

---

## Authentication

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/auth/register` | Returns a token. 201. |
| `POST` | `/api/auth/login` | Returns a token |
| `GET` | `/api/auth/me` | The current user, including preferences |
| `PATCH` | `/api/auth/preferences` | Update theme, language, default mode, top_k, etc. |
| `POST` | `/api/auth/logout` | 204 |

**Password rules:** at least 8 characters, at least one letter, at least one digit.
**Email validation:** `EmailStr`, so special-use TLDs (`.test`, `.invalid`) are rejected.

---

## Workspaces

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/workspaces` | **Only the caller's own workspaces** |
| `POST` | `/api/workspaces` | 201 |
| `GET` | `/api/workspaces/{id}` | 404 if not yours |
| `PATCH` | `/api/workspaces/{id}` | 404 if not yours |
| `DELETE` | `/api/workspaces/{id}` | 204. Cascades to documents, chunks, conversations, vectors |

---

## Documents

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/documents/supported-types` | The 8 formats, with accepted extensions |
| `POST` | `/api/workspaces/{id}/documents` | **Upload.** 415 on bad type, 413 on oversize |
| `GET` | `/api/workspaces/{id}/documents` | List a workspace's documents |
| `GET` | `/api/documents/{id}` | One document, with status and stage |
| `DELETE` | `/api/documents/{id}` | 204. Removes chunks **and** vectors |
| `GET` | `/api/documents/{id}/progress` | Current ingestion state (polling) |
| `GET` | `/api/documents/{id}/progress/stream` | **SSE.** Real-time stage transitions |
| `GET` | `/api/documents/{id}/chunks` | The stored chunks, with provenance |
| `GET` | `/api/documents/{id}/chunk-stats` | Chunk count, size distribution, coverage |
| `POST` | `/api/documents/{id}/reindex` | Re-embed with the current model |
| `POST` | `/api/documents/preview-chunks` | **Chunk without storing.** Powers the Learning Mode lab |
| `GET` | `/api/workspaces/{id}/stale-documents` | Documents embedded with a different model |

### The SSE endpoint

`EventSource` cannot set an `Authorization` header, so this endpoint accepts the token as
a query parameter:

```
GET /api/documents/{id}/progress/stream?token=<jwt>
```

The frontend uses SSE when available and **falls back to polling** `progress` if the
stream drops. Both read the same database row, so they can never disagree.

---

## Chat

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/chat/ask` | **The main endpoint.** Full pipeline. |
| `POST` | `/api/chat/retrieve` | Retrieval only — no generation. Powers the Playground |
| `GET` | `/api/conversations` | List conversations |
| `POST` | `/api/conversations` | Create one in a workspace |
| `GET` | `/api/conversations/{id}` | With messages |
| `PATCH` | `/api/conversations/{id}` | Rename, change mode |
| `DELETE` | `/api/conversations/{id}` | 204 |
| `GET` | `/api/messages/{id}/trace` | **The RAG Trace** for one answer |
| `DELETE` | `/api/messages/{id}` | 204 |

### `POST /api/chat/ask`

```json
{
  "question": "How does virtualization improve resource utilization?",
  "workspace_id": 3,
  "conversation_id": null,
  "mode": "offline",
  "top_k": 5,
  "candidate_k": 20,
  "document_ids": null,
  "use_rerank": false
}
```

`workspace_id` is **required** — there is no "search everything" mode. The optional
retrieval overrides exist for the Playground and Developer Mode.

The response contains the answer, the provider that **actually** served it
(`provider`, `model`, `used_fallback`, `fallback_reason`), the citations, the grounding
verdict, and the retrieval summary.

### `POST /api/chat/retrieve`

Same request shape minus the conversation fields. Returns scored candidates, the built
context, and a trace — **with no language model involved**. This is the endpoint that
makes retrieval inspectable on its own.

---

## Features (presentation transforms)

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/features/languages` | The 7 supported languages |
| `GET` | `/api/features/shorten-levels` | `normal` / `short` / `very_short` |
| `GET` | `/api/features/capabilities` | What is enabled right now, and why not |
| `POST` | `/api/features/translate` | Produces an `AnswerVariant` |
| `POST` | `/api/features/shorten` | **422 if validation fails** — see below |
| `POST` | `/api/features/speech` | Speakable text, citation markers stripped |
| `POST` | `/api/features/explain` | A learning-mode explanation |
| `GET` | `/api/features/messages/{id}/variants` | Cached transforms |
| `DELETE` | `/api/features/messages/{id}/variants` | 204 |

**All of these operate on the canonical answer and never modify it**
([08](08_citations_and_canonical_answer.md)).

**Shorten can legitimately fail.** If the compressed text loses a number, a warning, a
concept or a citation, the endpoint returns **422** and the original is kept:

```json
{
  "error": {
    "code": "shorten_rejected",
    "message": "The shortened version could not be verified as preserving the original's meaning and citations, so the original answer has been kept.",
    "detail": { "issues": ["The result is 89% of the original length, which is not short enough for the 'Short' level..."] }
  }
}
```

This is a feature. Returning a silently degraded answer would be the bug.

---

## Evaluation

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/evaluation/security` | Runs the 6 security self-tests against the live system |
| `POST` | `/api/evaluation/retrieval` | Computes Recall@K, Precision@K, MRR, MAP from a labelled set |
| `GET` | `/api/evaluation/history` | Past runs |
| `DELETE` | `/api/evaluation/history/{id}` | 204 |
| `GET` | `/api/evaluation/metrics-reference` | What each metric means and why it matters |

`POST /api/evaluation/retrieval` **requires** `relevant_chunk_ids` per question. If you
post an empty dataset you get a clear error — **not** an invented score
([14](14_evaluation.md)).

---

## Analytics

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/analytics/overview` | Documents, chunks, queries, providers |
| `GET` | `/api/analytics/retrieval` | Score distribution, faithfulness, latency percentiles |
| `GET` | `/api/analytics/activity` | Queries over time |

All numbers come from `query_logs` and `trace_events` — real recorded data. Where a
metric is a proxy rather than a measurement, the response says so in a `caveats` field,
and the UI renders those caveats verbatim.

---

## Settings

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/settings` | Effective configuration (**secrets redacted**) |
| `GET` | `/api/settings/rag` | Chunking, retrieval, grounding parameters |
| `GET` | `/api/settings/providers` | Which providers are configured and available |
| `GET` | `/api/settings/providers/models` | **Live** model lists from each provider |
| `POST` | `/api/settings/local-model/load` | Load the GGUF now |
| `POST` | `/api/settings/local-model/unload` | Free the RAM |
| `GET` | `/api/settings/system` | Versions, paths, vector count, model status |
| `GET` | `/api/settings/languages` | Supported languages |

**`/api/settings` never returns a key.** It reports whether one is *configured*, never
its value.

`/api/settings/providers/models` queries the providers live, so a configured model ID can
be checked against reality rather than assumed from documentation.

---

## Trying it by hand

```bash
# Register and capture a token
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"CarinaaTest123","display_name":"Demo"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Create a workspace
WS=$(curl -s -X POST http://127.0.0.1:8000/api/workspaces \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Demo"}' | python -c "import sys,json; print(json.load(sys.stdin)['id'])")

# Upload a document
curl -s -X POST "http://127.0.0.1:8000/api/workspaces/$WS/documents" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@samples/cloud_computing_notes.md"

# Retrieval only - no model involved
curl -s -X POST http://127.0.0.1:8000/api/chat/retrieve \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"workspace_id\":$WS,\"question\":\"What is a hypervisor?\"}"

# Ask a real question, offline
curl -s -X POST http://127.0.0.1:8000/api/chat/ask \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"workspace_id\":$WS,\"question\":\"What is a hypervisor?\",\"mode\":\"offline\"}"
```

---

## Next

→ [13 — Frontend Guide](13_frontend_guide.md)
