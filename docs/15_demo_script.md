# 15 — Demo Script

A step-by-step presentation plan. Roughly **12–15 minutes** if you follow it as written,
with timings marked so you can cut sections under pressure.

---

## Before you start (do this 10 minutes early)

```bash
# 1. Start the backend
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 --app-dir backend

# 2. Build and serve the frontend from the same origin (avoids running two servers)
cd frontend && npm run build && cd ..

# 3. Confirm it is healthy
curl -s http://127.0.0.1:8000/api/health
```

Then, in the browser:

- Sign up with a demo account.
- Create a workspace called **"Cloud Computing Notes"**.
- Upload `samples/cloud_computing_notes.md` and **wait until it says Ready**.
- **Ask one warm-up question** so the local model is loaded into RAM. This matters: the
  first offline query loads 2 GB from disk, which takes tens of seconds. Doing it now
  means it is fast on stage.
- Open `http://127.0.0.1:8000` — not the Vite dev server. One origin, no proxy to
  explain.

### Have these ready in tabs

1. The app, logged in, on the Dashboard
2. `/docs` (the API reference)
3. A terminal with the backend directory as the working directory

### Decide your mode

| If the venue has… | Use | Because |
| --- | --- | --- |
| Reliable internet + an API key | **Online** | Fast, high-quality answers |
| No internet, or you want the stronger story | **Offline** | No network at all — and you can prove it |

**Offline is the better demo if you have time.** The offline guarantee is the most
distinctive thing in this project, and it is the one an examiner will not have seen
elsewhere. Budget for slower answers (tens of seconds each).

---

## Part 1 — The problem (1 min)

**Say this:**

> "A language model has read the internet, but it has not read *your* documents. Ask it
> about your lecture notes and it will not say 'I don't know' — it will invent something
> that sounds completely authoritative. That is a hallucination, and it is not a bug that
> gets patched. It is the model doing what it was trained to do.
>
> Carinaa fixes that by making the model answer from evidence it is given, and then
> checking that the answer actually matches the evidence."

**Then:** show the Landing page. Point at the tagline — *Every Answer, Traceable*.

---

## Part 2 — Ingestion (2–3 min)

**Open Knowledge Base. Upload a document.**

**Say this while it runs:**

> "Ingestion runs once per document. Watch the stages: parsing, chunking, embedding.
> These are real stage transitions written to the database — not a timer. I can prove it."

**Prove it:**

1. Open a **second browser tab** on the same page. Both show the same stage.
2. **Say:** "Both tabs are reading the same database row. A fake progress bar would show
   two different animations."

**Then open the Document Viewer.**

> "These are the actual chunks that were stored. Every one carries where it came from —
> page number, or slide, or sheet and row range. That provenance is what makes citations
> possible later."

**Point at the chunk boundaries.**

> "Chunking is where retrieval quality is decided. Too big, and one vector has to
> represent three topics — it becomes a blurry average and matches nothing well. Too
> small, and there is no context. This is the middle."

**Optional, if time allows — the chunking lab.** Learning Mode → live chunking lab. Move
the size slider and watch the chunk count and boundaries change.

---

## Part 3 — The RAG Laboratory (3–4 min) ⭐

**Go to RAG Laboratory (formerly Playground).** Do this *before* the answer, because it
explains the machine that produces the answer.

**Open the Embeddings bench and press "Embed and compare". Say this:**

> "This is the idea most people find hardest: a sentence becomes a list of numbers, and
> meaning survives. These two sentences are about the same thing — look, 0.72. These two
> are about completely different things — 0.04. That single number is what the entire
> search is built on."

**Then point at the scatter plot, and say the honest thing about it:**

> "This plot is a projection — two dimensions standing in for 384. The label tells you how
> much of the real structure it keeps, usually about 80%. Trust the matrix for distances;
> use the picture only for who is near whom. A teaching tool that hid that caveat would be
> teaching you something false."

**Open the Chunking bench and drag the chunk size down. Say:**

> "Smaller chunks, more of them — and each becomes one searchable unit. This is the first
> place an answer can go wrong, and it is completely invisible in a normal chat UI."

**Then the retrieval limit:**

1. Open the **Retrieval** bench. Run a query with re-ranking **off**. Note the chunks.
2. Turn re-ranking **on**. Re-run.
3. **Say:**

> "The same chunks, in a different order. That is the hard limit of re-ranking: it can
> only reorder what retrieval already found. It cannot recover a chunk that retrieval
> missed. If the right passage was not in the top 20, re-ranking will never see it.
> That is why Recall@K is the metric that matters most — and why we measure it separately."

This section earns more credit than a slicker demo would. It shows you understand what
your own system cannot do.

**If you have time — the Full Pipeline bench.** Run a question and use **Pause**. Step
through the stages one at a time. It is the best bench to present from, because every
number on screen was measured during the run you are looking at.

---

## Part 3b — Learning Mode, in the chat (1 min)

**In Chat, turn on the Learning toggle in the header. Ask a question.**

> "The answer is the product. This is the laboratory underneath it — the same pipeline,
> for this exact answer, fetched from the server. Every stage is clickable. And notice
> `reranking` says 'skipped': it did not run, so the system says so rather than showing
> me an animation of something that never happened."

That last sentence is worth memorising. It is the whole design principle in one line.

---

## Part 4 — The answer, and everything behind it (3 min)

**Go to Chat. Ask a question the document answers well.**

**Point at the answer and, in order:**

1. **The provider badge** — "This names the provider that actually answered. If Gemini
   failed and Groq took over, it says 'Groq · Fallback'. It never hides a fallback."

2. **The grounding badge** — "This is not decoration. It says whether the answer is
   supported by the evidence."

3. **The citations** — click one. "This resolves to a real chunk in a real document at a
   real page. It is not a number the model made up."

4. **Then click Trace.**

**Walk down the waterfall:**

> "Nine stages. Every duration here is measured, not estimated. And notice the bar for
> generation — it is enormous compared to everything else. That is the single most
> important performance fact about this system: retrieval takes milliseconds, generation
> takes tens of seconds. Everything else is effectively free."

**Point at `reranking`:**

> "It says 'did not run'. We could have hidden it. But hiding a stage implies it ran."

---

## Part 5 — The refusal (1 min) ⭐

**Ask a question the documents do not cover.** For the sample notes, try:

> "What is the capital of Peru?"

**Wait for it. Then:**

> "The documents do not contain this. And it said so. It returned
> INSUFFICIENT_EVIDENCE and refused to answer, instead of inventing something.
>
> **A RAG system that cannot say 'I don't know' is not a RAG system. It is a chatbot
> with a search box.**"

**This is the moment that distinguishes Carinaa from a wrapper around an API.** Do not
rush it.

---

## Part 6 — The transforms, and the canonical answer (2 min)

**On the same answer, use the toolbar:**

1. **Translate** → Tamil. "Citations are preserved — `[1]` stays `[1]`, because it points
   at the original source, which is not translated."
2. **Switch back to English.** "The original is byte-for-byte identical. Translations are
   stored as *variants*. The canonical answer is never modified — because if a transform
   overwrote it, you would be translating a translation, and grounding would become
   meaningless."
3. **Shorten.** If it returns a shortened answer, good. **If it rejects with 422, that is
   the better outcome — say so:**

> "It refused to shorten, because the compressed version lost something it judged
> important. It kept the original instead of handing me a shorter answer that quietly
> dropped a number. That refusal is the feature."

4. **Read Aloud.** "This uses the browser's own speech synthesis. No API, no network, and
   it strips the citation markers so the voice does not read 'bracket one'."

---

## Part 7 — Offline, and the proof (2–3 min) ⭐⭐

**Switch to Offline mode. Ask the same question.**

> "Same pipeline. Same retrieval. Same citations. Same grounding. The only thing that
> changed is the provider — and it is running entirely on this laptop."

**Then the proof:**

```bash
cd backend
../.venv/Scripts/python.exe -m pytest tests/test_offline_network.py -q
```

**Say this while it runs:**

> "That is not a promise in a README. Four independent tests: the offline code path
> contains no reference to any online provider; the local model module imports no HTTP
> client at all; offline generation runs while every socket operation raises; and the
> online providers are replaced with objects that fail the test if they are ever touched."

**Then, if the venue allows — disconnect the network and ask again.** It still works.
**That is the moment to slow down for.**

**And the honest footnote:**

> "It is not that we remember not to fall back. The offline method contains no reference
> to Gemini or Groq at all. There is nothing to fall back *to*."

---

## Part 8 — Security (2 min)

**Open Settings → Security (or the Evaluation page).**

> "These are six probes that run against the live system right now. They are not
> assertions in a document. If one cannot run, it reports SKIPPED with the reason — it
> never passes by default."

**Point at the results: 6/6 passed, 0 skipped.**

**Then the workspace isolation story:**

> "A workspace is a hard boundary. It is enforced at four layers: authentication,
> ownership validation, a filter inside the vector index, and a re-check on every
> returned row. The last two are redundant on purpose — they fail differently.
>
> And requesting someone else's workspace returns **404, not 403**. A 403 would confirm
> the workspace exists, which turns the endpoint into a way to enumerate other people's
> data."

**The honest caveat — say it before you are asked:**

> "This does not prove the system is secure. It proves six specific properties held at
> the moment they ran. Prompt injection cannot be eliminated — what we can do is make the
> blast radius small, because the prompt never contains a secret."

---

## Part 9 — Evaluation, honestly (1–2 min)

**Open Analytics.**

> "Every number here is computed from recorded queries. And where a metric is a proxy
> rather than a measurement, we say so — the UI renders the backend's caveat verbatim.
>
> We do not show an accuracy percentage. We cannot measure accuracy without labelled
> data, and inventing a number would be worse than showing nothing."

**Point at the em dashes.**

> "Missing data shows as an em dash, not as zero. A zero is a claim. An em dash is an
> admission."

**Then the test suite:**

```bash
cd backend
../.venv/Scripts/python.exe -m pytest -q
```

> "27 tests. 18 of them are about workspace isolation alone."

---

## Part 10 — Close (1 min)

> "Carinaa is a RAG system that is honest about what it knows.
>
> It cites real sources. It tells you when the evidence is weak. It refuses when the
> documents do not cover the question. It runs with no internet at all, and it can prove
> that. And it is built so that every stage is inspectable — not hidden behind a
> framework."

---

## Questions you should expect

**"How is this different from ChatGPT with a file upload?"**
> "Three things. It cites the exact chunk it used. It checks whether the answer is
> supported by that chunk and tells you when it is not. And it runs entirely offline with
> no API key — which you can verify by disconnecting the network."

**"What is the accuracy?"**
> "We do not report a single accuracy figure, because it would not mean anything. What we
> report is Recall@K and Precision@K against a labelled dataset you build yourself, plus
> faithfulness and citation correctness from real queries. Inventing a percentage would be
> the easiest thing in this project to fake and the least useful."

**"Why not just use a bigger model?"**
> "A bigger model writes better prose. It does not make the answer traceable. The problem
> we are solving is not fluency — it is verifiability. A 400-billion-parameter model that
> cannot cite a source has exactly the same problem as a 3-billion one."

**"Why does the local model take 34 seconds?"**
> "Because it is 3 billion parameters running on a CPU with no GPU, which is what makes it
> run on any laptop with no setup. The interesting number is not the 34 seconds — it is
> that every other stage in the pipeline takes milliseconds. Generation is the bottleneck
> by three orders of magnitude."

**"What would you do next?"**
> "Hybrid search — combining keyword search with vector search. It attacks Recall@K, which
> is the metric that actually limits quality. Re-ranking only reorders what retrieval
> already found, so it raises precision and cannot raise recall. Fixing recall is the
> higher-leverage change."

**"Did you find any bugs?"**
> "Two, both in the security reporting. One query selected trace payloads with no user
> filter, so a user's security report scanned every tenant's data. And the cross-workspace
> authorization check was hard-coded to skip, so it never ran. Both are fixed, and both
> have regression tests." *(This is a good question to get — it shows you tested your own
> claims.)*

---

## If something breaks on stage

| Symptom | Cause | Fix |
| --- | --- | --- |
| First offline answer takes ~60 s | The model is loading 2 GB from disk | It was not warmed up. Wait — it is a one-time cost. Or warm it in advance |
| "No online provider is configured" | No API key | Switch to Offline mode. **This is a feature, not a failure — say so** |
| Answer looks truncated | Context budget or model output limit | Lower `top_k` in the Playground |
| Retrieval returns nothing | The document is not `ready` yet | Check the Knowledge Base; wait for Ready |
| Everything 404s | Backend not running | `curl http://127.0.0.1:8000/api/health` |

**The general rule:** if something fails, **explain the failure instead of hiding it**.
The refusal and the honest error message are the product. A demo that only shows the happy
path demonstrates less than one that shows a system correctly declining.

---

## The three moments that matter

If you remember nothing else:

1. **Part 5 — the refusal.** The system says "I don't know" instead of inventing.
2. **Part 7 — the offline proof.** Tests, not promises, and then the network cable.
3. **Part 3 — the re-ranking limit.** You explain what your own system cannot do.
4. **Part 3b — the skipped stage.** The system says "did not run" instead of animating
   something that never happened. This is the line that separates a teaching tool from a
   mock-up, and it lands well because the audience can check it.

Everything else is supporting material.

---

## Quick reference: where each claim is demonstrated

| Claim | Where to go | What to point at |
| --- | --- | --- |
| Search is by meaning, not keywords | Embeddings bench | 0.72 vs 0.04 in the matrix |
| Chunking is invisible but decisive | Chunking bench | chunk count changing with the slider |
| Re-ranking cannot add anything | Retrieval bench | same chunks, new order |
| Citations are real | Generation bench | each `[n]` resolved to a location |
| The answer is checked, not trusted | Generation bench | the grounding verdict and its reason |
| The system does not bluff | Chat | `INSUFFICIENT_EVIDENCE` refusal |
| Nothing is faked | Chat (Learning Mode) | a stage marked **skipped** with its reason |
| Offline really is offline | Settings → Offline + the test suite | no network calls, proven by test |
| Quality is measured, not asserted | Analytics | metrics with their `caveats` rendered |

---

## Back to the beginning

← [Documentation index](README.md)

---

## Click-through order for the demo (3 minutes)

1. **Chat → ask a normal question.**
   *"What does UNIT III say?"*
   Point out: the provider badge says **Gemini**, role *primary*. It used the configured model directly.

2. **Open Sources.** Show the citation resolves to a real location
   (`UNIT III – Concept Generation`). Click the citation → it scrolls to the source card.

3. **Open Retrieval Details.** Show the measured numbers: candidates, excerpts, top score, scope.

4. **Ask a page question:** *"Tell me what is on page number 3."*
   Point out: page filter applied → only chunks covering page 3 were searched.

5. **Test memory:** *"My name is Abishek." then "What is my name?"*
   Answer: *"Your name is Abishek."* — from the conversation, not the documents.

6. **Toggle Learning Mode.** Ask another question. The panel opens itself and steps through
   the stages; the answer appears after the walkthrough reaches generation.

7. **Show the fallback (if Gemini is rate limited):** the provider reads *Groq · Fallback*,
   and the trace shows which Gemini models were skipped and why.

### What to say about the hybrid

> Gemini and Groq each have a chain of models. Rate limits apply per model, so if one is
> busy we try the next. Whichever model answered is recorded in the trace, so you always
> know which model wrote the answer. Gemini is primary; Groq takes over only when Gemini
> cannot serve the request.

