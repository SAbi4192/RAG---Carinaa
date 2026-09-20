# 09 — Offline & Online Modes

---

## The adapter pattern

```
RAG pipeline  →  LLM adapter  →  provider
```

The pipeline hands over a list of messages and receives text. It does not know, and
cannot find out, whether Gemini, Groq or a local GGUF file produced it.

**Why this matters:**

1. **Offline mode is a routing decision, not a rewrite.** The same pipeline runs; only
   the provider behind the adapter changes.
2. **Swapping a deprecated model ID is a config change in one file**, not a
   search-and-replace across the codebase.
3. **The pipeline is testable without a provider.** The tests inject stub providers.

**Where does it fit?**
`backend/app/llm/adapter.py` → `gemini.py` / `groq.py` / `local.py`

---

## Online mode

```
RAG context → LLM adapter → Gemini
                              │ failure (rate limit, outage, bad key)
                              ▼
                            Groq
```

**Gemini is primary. Groq is the fallback.**

### The honesty rule

> The fallback is **always disclosed**.

`LLMResponse` carries `used_fallback` and `fallback_reason`. When Groq answers because
Gemini failed, the UI shows:

```
Groq · Fallback
```

It never shows "Gemini". It never hides the fallback. Every answer names the provider
that **actually** served it.

**Why this is a rule and not a nicety:** a user comparing answer quality across a
session needs to know which model wrote what. A silent fallback makes that impossible,
and it makes the primary's failure invisible to the people who should fix it.

The trace records the same thing in more detail — which providers were attempted, in
what order, and why the first one failed.

**How to demonstrate it**
Configure only `GROQ_API_KEY` (leave Gemini blank). Ask a question. The badge reads
"Groq · Fallback", and the trace's `llm_generation` stage reports
`primary_attempted: "none"` with the reason "Gemini was not configured."

---

### Silent truncation: why `GEMINI_THINKING_BUDGET` exists

This is the subtlest bug in the project, and it is a good one to explain in a viva.

**Gemini's reasoning tokens are charged against `maxOutputTokens`.**

`gemini-3.6-flash` is a *thinking* model. Before it writes a visible answer it produces
internal reasoning tokens, and those tokens come out of the **same** output budget you
set. So this is not a large budget for a small task — it is a budget shared between the
model's private reasoning and the answer you actually want:

```
maxOutputTokens = 1024
thoughtsTokenCount = 777      # spent on reasoning
candidatesTokenCount = 55     # left for the answer
finishReason = "MAX_TOKENS"   # stopped here
```

The visible answer was **truncated mid-sentence** — a Tamil translation stopped at
`* **Type 1 (bare-metal):**` and never reached Type 2. Crucially, Gemini does **not**
raise an error when this happens. It returns HTTP 200 with a `finishReason` of
`MAX_TOKENS`, and the truncated text reads like a finished answer.

**The symptom that led here** was an end-to-end failure that looked like a translation
bug:

```
[FAIL] Translation preserves citations
       original=['1', '2'] translated=['2']
```

The translation had not "decided" to drop citation `[2]`. It simply never got that far.

**The fix, in two parts:**

```python
output_budget  = max_tokens or settings.gemini_max_output_tokens
thinking_budget = settings.gemini_thinking_budget

generation_config = {
    "maxOutputTokens": output_budget + max(0, thinking_budget),   # allowance ON TOP
}
if thinking_budget >= 0:
    generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}
```

1. The thinking allowance is added **on top of** the caller's output budget, so reasoning
   can never eat the answer.
2. `GEMINI_THINKING_BUDGET=0` disables thinking outright. Measured effect: `thoughts=0`,
   and the output was **more** complete (231 characters vs 176).

`-1` means "let the model decide". `0` is the default because translation and
shortening want determinism, not deliberation.

**Truncation is now reported, never hidden.** Every provider normalises its own
vocabulary for "I ran out of room" into one helper:

| Provider | Its word for "truncated" |
| --- | --- |
| Gemini | `finishReason: MAX_TOKENS` |
| Groq (OpenAI-compatible) | `finish_reason: "length"` |
| llama.cpp (offline) | `finish_reason: "length"` |

```python
def is_truncated(finish_reason): ...        # app/llm/base.py
response.truncated                          # property on LLMResponse
```

This mattered immediately. When Gemini rate-limited and the Groq fallback produced a
Tamil translation that stopped mid-sentence, the response carried **no warning at all**
and validation passed — because only Gemini had been taught to look.

What the app now does with the flag:

- **Translate** attaches a visible warning: *"The provider stopped generating before it
  finished, so this translation may be incomplete. The original grounded answer is
  unchanged."*
- **Shorten** treats truncation as a **failed validation** and keeps the original. A
  compression that was cut off cannot honestly be called a verified compression.
- The flag appears in `LLMResponse.as_dict()` and therefore in the RAG Trace.

**How to demonstrate it**

```bash
# Watch the warning appear when the output budget is genuinely too small.
# Set GEMINI_THINKING_BUDGET=-1 and GEMINI_MAX_OUTPUT_TOKENS=64, then ask a
# question that needs a long answer. The server logs:
#   "Gemini hit the output limit, so the answer was truncated
#    (maxOutputTokens=..., thoughts=..., output=...)"
```

---

## Offline mode

```
RAG context → LLM adapter → local llama.cpp   (and nothing else)
```

**Model:** Qwen2.5-3B-Instruct, Q4_K_M, 36 layers, 32,768-token context window,
weights in `models/llm-model.gguf` (~2 GB).

**Why llama.cpp / GGUF:**

- Runs on **CPU**. No GPU, no CUDA, no cloud account.
- The weights are a **single file on disk**. Nothing is downloaded at query time.
- 3B parameters at Q4_K_M fits in ~2 GB of RAM — a student laptop.

The GGUF's properties were verified by reading its header directly
(`scripts/inspect_gguf.py`), not assumed from the filename.

---

### ⚠ The offline guarantee

> **Offline mode must never silently switch to an online provider.**

This is the single most important guarantee in the product, and it is enforced
**structurally** rather than by discipline:

> `_generate_offline` contains **no reference to Gemini or Groq at all**.
>
> It is not that we remember not to fall back. **There is nothing to fall back to.**

If the local model cannot load, the adapter either raises a clear error or produces a
clearly-labelled **extractive** answer built only from retrieved evidence. Both are
honest. Neither goes online.

**Where does it fit?**
`backend/app/llm/adapter.py` (`_generate_offline`), `backend/app/llm/local.py`,
`backend/app/rag/failsafe.py`

---

### How the guarantee is proven

Three independent proofs, because an absolute claim needs more than one.

#### 1. Structural (AST)

A test parses `adapter.py` and inspects the `_generate_offline` function body:

```python
for forbidden in ("gemini", "groq", "_generate_online", "AllProvidersFailed"):
    assert forbidden not in referenced
```

If someone later adds a fallback, this fails immediately.

#### 2. Static (imports)

`app/llm/local.py` is checked for network-capable imports:

```python
_FORBIDDEN_IMPORTS = {"requests", "httpx", "urllib", "socket", "aiohttp", ...}
```

A llama.cpp wrapper has no business importing an HTTP client. If one appears, the test
fails.

#### 3. Runtime (sockets + tripwire)

The strongest proof. Offline generation is run while:

- **every socket operation raises** — `connect`, `create_connection`, and
  `getaddrinfo`, so even a DNS lookup counts as a violation, and
- the online providers are replaced with objects that **fail the test if touched**.

If the answer still arrives, and no socket was opened, and no provider was touched, the
offline path provably used no network.

```
[PASS] Offline path makes no external network calls
[PASS] test_offline_generation_touches_no_network_and_no_online_provider
[PASS] test_real_local_model_generates_with_networking_disabled
```

The last one runs the **real 2 GB model** with networking disabled and asserts it still
produces text.

**Run them yourself:**

```bash
cd backend
../.venv/Scripts/python.exe -m pytest tests/test_offline_network.py
../.venv/Scripts/python.exe -m pytest -m slow     # includes the real model
```

---

### A subtlety worth understanding

The socket-blocking test has an ordering constraint that is easy to get wrong.

On Windows, **creating a proactor event loop opens a loopback socketpair internally**.
So if you patch `socket.socket.connect` *before* `asyncio.run(...)`, you break asyncio's
own bootstrap — and the test fails for a reason that has nothing to do with the code
under test.

The fix is to create the loop first, then patch, then run:

```python
loop = asyncio.new_event_loop()
with _network_disabled() as attempts:
    response = loop.run_until_complete(adapter.generate(..., mode="offline"))
loop.close()
```

This is documented in `backend/tests/test_offline_network.py`. It is a good example of a
test that must be *designed* rather than written, because a careless version of it tests
the test harness instead of the system.

---

### Why offline mode has its own prompt

This is the most instructive bug in the project, because **the code was not broken — the
prompt was wrong for the model**.

**The symptom.** Offline mode appeared to "not return a response". It did return one; the
response was a refusal:

```
Online  (Groq)  "Tell me about the PDF"
        -> "The PDF is ADT_Notes.pdf, covering Systematic Concept Generation,
            Document and Communicate, Evaluation of Technology Alternatives ..."  [1] [2]

Offline (Qwen 3B)  "Tell me about the PDF"
        -> "I could not find this in the documents in this workspace."
```

Retrieval could not be the cause. **Embeddings and the vector store are local in both
modes** — the only thing that changes is the language model. And the retrieval scores were
comparable: the online answer that worked fine had a top score of `0.2749`, while the
offline refusal had `0.2887`. Retrieval was equally mediocre in both cases; only the model
differed.

**The cause.** Both modes shared one system prompt — a six-rule rulebook written for
frontier models. Measured on the same chunks, the same question:

| Prompt | Result |
| --- | --- |
| Shared production prompt | refused, 3/3 runs |
| Prompt written for a 3B model | answered, 3/3 runs |

The 3B model was not incapable. It was overwhelmed. Three specific things caused it, each
confirmed by experiment (`scripts/tune_offline_prompt.py`):

1. **Length.** The production system prompt is 2,024 characters; the offline one is 198.
   Every token spent on rules is attention not spent on evidence, and a 3B model's
   attention over a ~1,800-token prompt is weak.

2. **An escape hatch in the system prompt.** The production rules hand the model one exact
   sentence to say when it "cannot find the answer", and declare anything the context is
   silent on "out of scope". Faced with a broad question, the cheapest path is to declare
   the context silent. Counter-intuitively, moving that sentence *into the system prompt*
   made refusals **worse** (it became a standing instruction), so in the offline prompt it
   lives at the end of the user message, phrased as a last resort.

3. **No licence to summarise.** "Tell me about the PDF" has no single answer sentence to
   find, so a literal reading says the context does not answer it. The production prompt
   never authorises a summary; the offline one does.

**What did not change.** Online mode is untouched — same system prompt, same user block,
still the default. `backend/tests/test_offline_prompt.py` asserts this, so a future edit
cannot quietly degrade the working path. The offline prompt also keeps every guarantee:
citations, the untrusted-data rule, and the refusal for genuinely unsupported questions
(verified against "Who is Donald Triumph?", which the documents do not mention).

**The general lesson.**

> A prompt is not portable across model sizes. Instructions a frontier model follows
> effortlessly can actively mislead a small one — and the failure looks like a bug in the
> retrieval, the routing, or the UI, never like a prompt problem.

---

### Two limits of the local model, measured

Both were found by reading real output rather than trusting the code, and both are worth
stating plainly because they are **model** limits, not defects to be fixed.

**1. It does not reliably stop.** Translating a 552-character answer into Tamil, the model
never emitted an end-of-sequence token. Every budget was consumed, with the output growing
linearly:

| Budget | Output | Ratio | `finish_reason` |
| --- | --- | --- | --- |
| 1024 | 948 chars | 1.72x | `length` |
| 1280 | 1183 chars | 2.14x | `length` |
| 1536 | 1397 chars | 2.53x | `length` |
| 2048 | 1857 chars | 3.36x | `length` |

There is no budget that makes it finish. So the goal cannot be "never truncated" — it is
**"never silently wrong"**. The response carries a visible warning, and the E2E asserts
*disclosure* rather than perfection.

**2. It can degenerate into gibberish that passes every check.** Given too much room, the
model produced a 3.8x expansion mixing English words into Tamil and looping:

```
நோetworks, servers, storage, applications, மற்றும் அழிவுகள்) உருவாக்குதல் ஒன்1் ...
```

Every citation and number still matched the source, so the existing checks — which gate on
citations and numbers — **all passed**. The translation was already computing a
`length_ratio`; nothing enforced it. It now does:

```python
MAX_TRANSLATION_LENGTH_RATIO = 3.0   # a faithful Tamil translation lands near 1.7x
MIN_TRANSLATION_LENGTH_RATIO = 0.30  # below this, content was dropped
```

This is a **degeneration detector, not a style rule**, and the bounds are deliberately
loose so a legitimate translation can never trip them.

**Related:** the output budget is now sized for the *target script*, not from the source
length alone. See `translation_token_budget` in `app/features/languages.py` — Tamil needs
roughly 2.4 tokens per source character, Italian about 0.7.

---

## The extractive failsafe

**What is it?**
When no model is available at all — no local GGUF, no API key — Carinaa can still
produce an answer by **quoting the retrieved evidence directly**.

**Why it exists:**
The alternative is an error message. But the evidence was retrieved successfully, and it
is genuinely relevant to the question. Quoting it is a real, useful answer.

**How it stays honest:**
The answer is clearly labelled. `provider` is `"extractive"`,
`is_extractive_failsafe` is `True`, and the UI presents it as an excerpt rather than as
generated prose. It is never passed off as a model's answer.

Controlled by `offline_extractive_failsafe` (default `True`).

---

## Choosing a mode

| | Online | Offline |
| --- | --- | --- |
| **Quality** | High | Moderate |
| **Speed** | Seconds | Tens of seconds on CPU |
| **Internet** | Required | **Not required** |
| **API key** | Required | **Not required** |
| **Cost** | Per token | Free |
| **Data leaves machine** | Yes | **No** |
| **Works on a plane** | No | **Yes** |

**The honest recommendation:** use Online when you have internet and a key — it is
faster and better. Use Offline when you need the guarantees, or when demonstrating that
the architecture genuinely does not depend on a cloud provider.

---

## How to demonstrate both

**The strongest demo sequence:**

1. Ask a question in **Online** mode. Note the provider badge and the latency.
2. Switch to **Offline** mode and ask the same question. Note the same pipeline, the same
   citations, the same grounding verdict — but a different provider and no network.
3. **Disconnect the machine from the network.** Ask again in Offline mode. It still
   works. This is the moment that lands.
4. Try Online mode with the network still disconnected. It fails with a clear message
   and **does not** silently fall back to the local model either — the mode you chose is
   the mode you get.

Step 3 is the one to slow down for.

---

## Next

→ [10 — Security & Workspace Isolation](10_security_and_isolation.md)
