# 07 — Grounding

> Referenced from `backend/app/rag/grounding.py`.

---

## What grounding is

Grounding checks whether the generated answer is **actually supported** by the evidence
that was retrieved.

It is the difference between:

- *"the model wrote something that sounds right"*, and
- *"the model wrote something we can point at a source for"*.

**Why do we need it?**
Because retrieval and generation are independent failure points. Retrieval can fetch the
wrong chunks. Generation can misread the right ones. Without a check, both failures
produce the same symptom: a confident, fluent, wrong answer.

Grounding is the stage that refuses to let that pass silently.

---

## What grounding is NOT

This matters more than what it is:

> **Grounding does not guarantee factual correctness.**

An answer can be perfectly grounded in a document that is itself wrong. Grounding
measures the relationship between the answer and the evidence — **nothing more**.

If a course note says "HTTP runs on port 8080", a grounded answer will say it too, and
correctly report `SUPPORTED`. Grounding is not a fact-checker; it is an
*evidence-relationship* checker.

The UI never claims otherwise, and neither should the demo.

---

## The four outcomes

| Status | Meaning |
| --- | --- |
| `SUPPORTED` | Every claim is cited, every citation is valid, and the cited text overlaps the claim |
| `PARTIALLY_SUPPORTED` | Citations are valid, but some claims are uncited or weakly supported by the excerpt they point at |
| `INSUFFICIENT_EVIDENCE` | Retrieval found nothing useful, or the model correctly refused because the documents do not cover the question |
| `CITATION_ERROR` | The answer cites something that does not exist in the retrieved set — a fabricated citation |

### Why four and not two

A boolean "grounded / not grounded" hides the distinction that matters most in practice.

`PARTIALLY_SUPPORTED` is the interesting one. It is the normal state of a real answer:
mostly supported, with a sentence or two that drifted. Collapsing it into "grounded"
would overstate confidence; collapsing it into "not grounded" would throw away a good
answer.

`INSUFFICIENT_EVIDENCE` is the one that proves the system is honest. **A RAG system that
cannot say "I don't know" is not a RAG system — it is a chatbot with a search box.**

---

## How support is measured

Lexical overlap, deliberately. For each sentence carrying a citation:

1. Take the **content words** of the sentence (stopwords removed).
2. Take the **content words** of the cited excerpt.
3. Measure how much of the sentence is covered by the excerpt.
4. Blend in **numeric agreement** — if the sentence contains numbers, do they appear in
   the excerpt?

The final score is a weighted blend:

```
support = 0.7 × (covered vocabulary / sentence vocabulary)
        + 0.3 × (numbers in the sentence that appear in the excerpt / numbers in the sentence)
```

Sentences shorter than 25 characters are not scored (there is nothing to check).
`grounding_min_overlap` (default `0.18`) is the threshold below which a claim counts as
weakly supported.

**Why the 0.3 numeric weight?** Because numbers are where paraphrase is most dangerous.
"The cache reduces latency by 30%" and "...by 60%" share almost all their vocabulary and
mean opposite things. Vocabulary overlap alone would call that supported. The numeric
term catches it.

---

## Why lexical overlap and not a second LLM call

A natural language inference (NLI) model, or asking a second LLM "is this supported?",
would be more accurate. It was rejected for four reasons:

| Reason | Why it matters here |
| --- | --- |
| **Deterministic** | The same answer always gets the same verdict. Essential when an examiner re-runs your demo |
| **Instant and free** | Adds milliseconds, not a second API call per answer |
| **Explainable** | You can show the actual word sets. "Here is why it said partially supported" |
| **Works offline** | No network call, so it functions in Offline mode |

**The trade-off, stated honestly:** a correct paraphrase with no shared vocabulary can
be scored as weakly supported. That is why a weak score produces
`PARTIALLY_SUPPORTED` rather than "unsupported", and why the raw number is surfaced so a
human can judge.

**If you want higher precision later:** swap `_score_support()` for an NLI model
(e.g. a small MNLI checkpoint). The interface is one function returning a float in
`[0, 1]`, so the rest of the pipeline is unchanged. Keep the lexical scorer as a
fallback for offline mode.

---

## Refusal detection

Separately from support scoring, grounding detects whether the model **refused**.

A refusal ("The provided documents do not contain information about X") is a *correct*
outcome when the evidence genuinely does not cover the question. It must not be scored
as a failure.

This is why the `INSUFFICIENT_EVIDENCE` path checks two things:

- retrieval found nothing above the relevance floor, **or**
- the model explicitly declined.

Both are honest. Both produce the same status, and the answer is marked `refused=True`
so the UI can present it as a considered answer rather than a failure.

---

## How it works end to end

```
answer + evidence excerpts + citation report + top retrieval score
                              │
                              ▼
              ┌───────────────────────────────┐
              │ 1. Did retrieval find anything?│
              │    no  → INSUFFICIENT_EVIDENCE │
              └───────────────┬───────────────┘
                              │ yes
                              ▼
              ┌───────────────────────────────┐
              │ 2. Any fabricated citations?   │
              │    yes → CITATION_ERROR        │
              └───────────────┬───────────────┘
                              │ no
                              ▼
              ┌───────────────────────────────┐
              │ 3. Did the model refuse?       │
              │    yes → INSUFFICIENT_EVIDENCE │
              └───────────────┬───────────────┘
                              │ no
                              ▼
              ┌───────────────────────────────┐
              │ 4. Score each cited sentence   │
              │    all strong → SUPPORTED      │
              │    any weak   → PARTIAL_...    │
              └───────────────────────────────┘
```

The order matters. Checking for fabrication **before** scoring support means a
fabricated citation produces `CITATION_ERROR` rather than being averaged into a
`PARTIALLY_SUPPORTED` verdict. A fabricated citation is a categorically different
problem and deserves its own status.

**Where does it fit?**
`backend/app/rag/grounding.py`, called from `backend/app/rag/pipeline.py`

**What if we removed it?**
Every answer would look equally trustworthy. The `PARTIALLY_SUPPORTED` signal — the one
that tells a user "check this one yourself" — would be gone.

---

## How to demonstrate it

Three queries against `samples/cloud_computing_notes.md`:

1. **A question the document answers well.**
   → `SUPPORTED` (or `PARTIALLY_SUPPORTED` with a clear reason). Citations resolve.

2. **A question the document does not cover** — e.g. *"What is the capital of Peru?"*
   → `INSUFFICIENT_EVIDENCE`, `refused=True`. **The system declines instead of
   inventing.** This is the most important single demo moment in the whole project.

3. **The Security page** runs a fabricated-citation probe: it feeds the resolver a
   citation pointing at an excerpt that does not exist, and shows it being caught as
   `CITATION_ERROR`. This proves the detection works without needing to trick a model
   into hallucinating on stage.

### The line to say during the demo

> "Grounding does not make the model correct. It makes the model's *relationship to its
> evidence* visible. When it says partially supported, that is the system telling you
> where to look — which is more useful than a green tick that means nothing."

---

## Next

→ [08 — Citations & the Canonical Answer](08_citations_and_canonical_answer.md)
