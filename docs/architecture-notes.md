# Architecture notes — ideas reviewed from WeKnora (and how they map to Carinaa)

**Status:** research note, not a work order. Nothing here has been adopted unless it
already exists in Carinaa. The point of this file is to record what was worth learning
from [Tencent/WeKnora](https://github.com/Tencent/WeKnora) and, honestly, why most of it
is deliberately *not* copied.

Carinaa is a single-user, local-first teaching RAG assistant whose whole product promise is
**traceability**: every stage measured, every citation resolved to a real chunk, and nothing
that fakes activity. WeKnora is an enterprise multi-tenant knowledge *platform* (Go backend,
Postgres/pgvector, Redis, an agent runtime, sandboxes, MCP, ~27 model vendors, IM channels).
The two share the RAG core but not the surrounding ambitions, so the useful comparison is
narrow and specific.

---

## 1. Hybrid retrieval + RRF — confirmed, already aligned

WeKnora: "Keyword + vector hybrid search, rerank, parent-child chunking and GraphRAG" as one
retrieval story, with recall/BLEU/ROUGE evaluation.

Carinaa already runs the same shape, independently built: dense (Chroma cosine) + sparse
(BM25 over the identical chunk population) fused with **Reciprocal Rank Fusion**
(`app/rag/hybrid.py`), then optional cross-encoder rerank. The fusion is deliberately
*displayed* — the RRF contribution from each side is kept (`rrf_score`, per-method rank) so a
human can see what fusion did rather than only its result.

**Note that reinforces our design, not changes it:** RRF is chosen because it is
score-scale-free. Dense cosine, BM25 and RRF all use different scales; WeKnora faces the same
problem. We keep `score_scale` on every retrieval result precisely so a UI can never print a
BM25 number under a "cosine similarity" label. The lesson held.

## 2. Parent-child chunking — worth adopting conceptually, not yet

WeKnora: "parent-child chunking" and "adaptive 3-tier chunking". The idea: retrieve a small
child chunk (high precision) but hand the model its larger parent (high context).

Carinaa's chunker currently emits one flat list of passages with page/section metadata.
Retrieval can be precise but the model loses surrounding context at a chunk edge.

**Assessment:** genuinely valuable, especially since page boundaries already give us a natural
parent (the page) and a natural child (the passage within it). This is a real future feature,
not a gap in the current brief. Deferred with the rest of "advanced RAG" per the documented
"Basic RAG must work and be explainable first" rule. If built: store `parent_id`/`char_start`
(already on Chunk) and, after scoring, replace each hit with its parent for the *prompt* while
citing the *child* — that asymmetry is the interesting part.

## 3. Rerank as a separate service + a "passage cleaning" step

WeKnora ships `rerank_server_demo.py` (rerank as an HTTP service) and a changelog line about
"passage cleaning for rerank" — trimming boilerplate before scoring a candidate.

Carinaa's reranker is in-process and optional. The transferable, cheap idea is the
**passage cleaning**: before reranking, strip repeated headers/footers/page numbers so the
cross-encoder scores content, not page furniture. We already normalise and de-duplicate
(`test_retrieval_dedupe.py`); a pre-rerank trim is a small, honest addition. Logged as a
candidate, not built here because it changes retrieval quality claims that would need
re-measuring.

## 4. Document-parsing trace timeline (Langfuse-style span tree)

WeKnora: "document parsing trace timeline (span tree with stage-by-stage progress +
stop-parse)" and Langfuse tracing of pipelines.

Carinaa already has the *concept* — the real pipeline event lifecycle (`app/rag/trace.py`,
`begin → running → terminal`, consumed live by Learning Mode and the RAG Trace page). We do
**not** add Langfuse: it is an external observability vendor, which contradicts
local-first-and-offline. Our measured durations are the same information, kept on-device.
The validation is that WeKnora's UI shows exactly the stage tree we built — it confirms the
Learning Mode direction was the right instinct, not that we should import the library.

## 5. Page-level provenance & citations — our strength, theirs generic

WeKnora has multimodal parsing and citations, but its public description does not emphasise
**structural page provenance** the way the brief demanded here. Carinaa keeps `page_number`/
`page_end` on every PDF chunk, threads it through retrieval, reranking, context, and citations,
and resolves "page 22" as a metadata filter rather than a semantic hope. That path is
deliberately stronger than a general platform's, because this product is *about* traceability.
No borrowing needed; this section exists to record that we compared and found ourselves ahead
on the specific axis.

## 6. Chunk editing with revision/rollback + diff

WeKnora: "retrieval chunks can be edited, diffed and rolled back." This is a governance feature
for a multi-tenant system.

Carinaa is single-user and treats the store as derived from the uploaded file. Editing chunks
would break the "answer points at real text" invariant unless edits were recorded as
provenance too. **Decision: not adopted** — the cost is correctness risk, the benefit is
multi-user, which is out of scope.

## 7. Scoped API keys / RBAC / OIDC / audit log

These are platform-security features for shared deployments. Carinaa is local, single-user,
workspace-scoped (isolation is enforced and tested). Adopting RBAC would be adding machinery
with no user. **Not adopted.** The *security-relevant* habit we did take seriously is the same
one WeKnora flags (SSRF-safe outbound, "whitelist-only mode", never expose the service to the
public internet). Web search is opt-in and outbound is to a fixed provider; nothing serves on
0.0.0.0.

## 8. Multimodal / large format breadth

WeKnora parses Word, PPT, Excel, EPUB, XMind, images, etc., via an "anydoc" in-process parser,
and has an ASR and a knowledge-graph (Neo4j) option. Carinaa handles the document formats the
teaching use-case needs and explicitly defers GraphRAG/multi-hop ("Advanced RAG is deliberately
deferred"). **Not adopted.** Each added parser is a maintenance and correctness surface, and the
brief was about fixing real root causes, not format count.

---

## Summary of what was kept

| WeKnora idea | Carinaa decision |
| --- | --- |
| Hybrid dense+BM25, RRF, rerank | Already built; design validated |
| Keep score scale honest | Already built (`score_scale`) |
| Parent-child / adaptive chunking | Real future feature; deferred (needs re-measure) |
| Passage cleaning before rerank | Small candidate; logged, not built (changes quality claims) |
| Parsing/pipeline span-tree tracing | Built in-process, offline — no Langfuse |
| Page-level provenance citations | We are *ahead* here; nothing to borrow |
| Chunk edit + revision/rollback | Not adopted (breaks single-source-of-truth) |
| RBAC / OIDC / scoped keys | Not adopted (wrong shape for single-user local) |
| SSRF-safe / public-network caution | Already honoured (opt-in web, loopback bind) |
| Multimodal formats, GraphRAG | Not adopted (scope, deferred by design) |

The most useful outcome of the review is negative in the right way: nearly every platform-level
feature we could have imported would have added surface area without serving a local-first
teaching tool's actual job. The few that did — RRF's score-scale honesty, the tracing-tree UI,
parent-child chunking as a next step — either confirm what Carinaa already does or mark the
one genuinely valuable future improvement (parent-child chunking), and that is written down here
so it is not "discovered" twice.
