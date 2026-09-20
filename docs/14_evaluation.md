# 14 — Evaluation

---

## The rule this document enforces

> **Do not invent evaluation scores.**

This is worth stating at the top, because it is the most common way a student project
misrepresents itself. A dashboard showing "Accuracy: 94%" that nobody measured is worse
than no dashboard. It is a fabricated claim presented as a measurement.

Every number Carinaa shows is computed from real recorded data, and where a metric is a
**proxy** rather than a measurement, the API says so in a `caveats` field that the UI
renders verbatim.

---

## Retrieval metrics

Retrieval quality is the metric that matters most, because **if the evidence is not
retrieved, nothing downstream can recover it** ([06](06_advanced_rag.md)).

### Recall@K

> Of the chunks a human marked relevant, what share appear in the top K?

**The most important retrieval metric.** It measures whether the evidence made it into
the candidate set at all.

### Precision@K

> Of the top K chunks returned, what share are actually relevant?

Measures how much noise is reaching the language model — which matters because context
budget is finite and irrelevant excerpts compete with relevant ones.

### MRR (Mean Reciprocal Rank)

> The mean of `1 / rank` of the first relevant chunk.

Rewards putting the right answer near the top. Position 1 scores 1.0, position 2 scores
0.5, position 8 scores 0.125. A big difference for a small ranking change.

### MAP (Mean Average Precision)

> The mean average precision across all relevant hits.

A rank-aware summary that rewards ordering *all* relevant chunks highly, not just the
first one.

---

## Generation metrics

### Faithfulness

> The share of answers whose grounding verdict was `SUPPORTED`.

A **proxy** for hallucination rate, measured against retrieved evidence
([07](07_grounding.md)). It is a proxy, not a human judgement, and it is labelled as one.

### Citation correctness

> The share of answers containing no fabricated citation.

A fabricated citation is worse than no citation, because it looks verifiable and is not.

### Refusal accuracy

> The share of answers that correctly declined when the documents did not cover the
> question.

A grounded system must be able to say "I don't know". This metric measures whether it
does so *correctly* — refusing when it should, and not refusing when it should not.

---

## Why Recall@K needs a human

There is no way to compute Recall@K without knowing which chunks are actually relevant.
That is a human judgement.

So `POST /api/evaluation/retrieval` **requires** the caller to supply
`relevant_chunk_ids` per question. Those ids come from the Document Viewer, where you can
read each chunk and decide.

If you post an empty dataset, you get a clear error — **not** a score of 0.87:

```json
{
  "error": {
    "code": "empty_evaluation_dataset",
    "message": "None of the supplied questions could be evaluated. Each one needs at least one relevant chunk id - look them up in the Document Viewer."
  }
}
```

This is a deliberate refusal. A retrieval metric computed against guessed labels is
theatre.

**Labelling by document is also supported.** Supply `relevant_document_ids` instead and
every chunk of those documents counts as relevant. It is coarser, but it is a useful
sanity check and much faster to produce.

---

## Running an evaluation

1. Upload a document to a workspace.
2. Open the **Document Viewer** and read the chunks. Note the ids that genuinely answer
   each question you plan to ask.
3. `POST /api/evaluation/retrieval`:

```json
{
  "workspace_id": 3,
  "name": "Baseline retrieval",
  "k": 5,
  "candidate_k": 20,
  "use_rerank": false,
  "dataset": [
    {
      "question": "What is a hypervisor?",
      "relevant_chunk_ids": [12, 13]
    },
    {
      "question": "How does virtualization improve resource utilization?",
      "relevant_chunk_ids": [9, 10, 11]
    }
  ]
}
```

The response includes the metrics, the per-question detail, and any skipped questions
with the reason they were skipped.

**Every run is stored** with its `embedding_model` and `rerank_enabled` flags. Storing
those two flags is what makes two runs comparable — otherwise you cannot tell whether a
metric moved because of a real change or because the configuration differed.

---

## What is reported, and what is honestly not

| Reported | Source | Type |
| --- | --- | --- |
| Recall@K, Precision@K, MRR, MAP | Labelled dataset | Measurement |
| Faithfulness | Grounding verdicts | Proxy, labelled |
| Citation correctness | Citation resolution | Measurement |
| Refusal accuracy | Grounding + retrieval | Measurement |
| p50 / p95 latency | `query_logs` | Measurement |
| Score distribution | `query_logs` | Measurement |
| Answer relevance | Lexical overlap | **Proxy** |

**Answer relevance is explicitly labelled a proxy**, and the caveat is blunt:

> Answer relevance is reported as a lexical-overlap proxy. It penalises correct
> paraphrases and must not be presented as a human quality judgement.

The UI renders that sentence as-is. It does not soften it.

---

## The security self-tests

A second, different kind of evaluation: six probes that run against the **live** system
and report what they observed. Detailed in
[10 — Security](10_security_and_isolation.md).

| Check | Result |
| --- | --- |
| Prompt injection containment | PASS |
| Fabricated citation detection | PASS |
| No secrets in trace payloads | PASS |
| Offline makes no external network calls | PASS |
| Cross-workspace vector isolation | PASS |
| Cross-workspace API authorization | PASS |

**6/6 passed, 0 skipped.**

The "0 skipped" is itself a result. The API authorization check used to be permanently
skipped due to a hard-coded `attacker_id=None` — a check that appears in a report and
never runs is worse than no check ([10](10_security_and_isolation.md)).

---

## The end-to-end suite

`scripts/e2e_test.py` runs the full user journey against a live server. Nothing is
mocked. Every assertion is against a real HTTP response.

```
register → create workspace → upload → poll ingestion
        → ask → inspect citations, grounding, trace
        → translate → shorten → speech → security checks → delete
```

**Current result: 40 passed, 0 failed, 40 checks.**

Highlights, all from real runs:

```
[PASS] Answer carries citations to real chunks
         1 citation(s)
[PASS] No fabricated citations
         invalid_numbers=[]
[PASS] Skipped stages are marked, not faked
         reranking=skipped
[PASS] Unsupported question does not get a fabricated answer
         status=INSUFFICIENT_EVIDENCE refused=True
[PASS] Translation does not modify the canonical answer
         original_unchanged=True
[PASS] Shortening rejected unsafe compression and kept the original
         HTTP 422 (rejected)
[PASS] Security self-tests executed
         6/6 passed, 0 skipped
```

```bash
.venv/Scripts/python.exe scripts/e2e_test.py --mode offline
```

---

## The pytest suite

```bash
cd backend
../.venv/Scripts/python.exe -m pytest              # 26 tests, fast
../.venv/Scripts/python.exe -m pytest -m slow      # + the real 2 GB model
```

| File | Tests | Covers |
| --- | --- | --- |
| `test_workspace_isolation.py` | 18 | Both isolation layers, plus the trace-scan leak regression |
| `test_offline_network.py` | 9 | The offline guarantee, four independent ways |

27 tests in total: 26 run in a few seconds, and one is marked `slow` because it loads the
real 2 GB model.

**A note on how these tests isolate themselves.** `conftest.py` redirects the app into a
throwaway directory by setting environment variables **before any `app.*` import**.
`settings` is a module-level singleton that reads the environment exactly once, at import
time — set them later and the app silently keeps using the real `data/` directory. The
tests blank the provider keys for the same reason, so a developer's populated `.env`
cannot leak into a test run.

---

## Measuring a change properly

If you modify chunking, the embedding model, or retrieval, do this:

1. **Record a baseline** with a labelled dataset. Recall@K is the number to watch.
2. Make **one** change.
3. Re-run the same dataset.
4. Compare. If Recall@K did not move, the change did not help retrieval — whatever it
   looks like in the UI.

Without a baseline, a change that "feels better" is indistinguishable from a change that
made things worse. That is the whole reason this page exists.

---

## Next

→ [15 — Demo Script](15_demo_script.md)
