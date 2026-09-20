# 08 — Citations & the Canonical Answer

---

## Part 1 — Citations

**What is it?**
A citation maps a `[n]` marker in the generated answer back to the real retrieved chunk
it came from — with document name, page, slide or row, and the actual snippet.

**Why do we need it?**
Because "according to your documents" is not a verifiable statement.

A citation is what turns an answer from a claim into a *checkable* claim. The user can
go to `cloud_computing_notes.md`, section "2. Virtualization", and read it themselves. If
the answer is wrong, they can see exactly where it went wrong.

For a product whose tagline is "Every Answer, Traceable", this is the feature.

**How does it work?**

1. **Extraction.** A regex pulls `[n]` markers out of the answer text.
   **Code fences are excluded** — `[0]` in a Python list or a JSON array is not a
   citation, and treating it as one produces nonsense citations pointing at real chunks.

2. **Validation.** Every extracted number is checked against the set of excerpts that
   were actually in the prompt. Numbers outside that range are **fabricated**.

3. **Resolution.** Valid numbers are joined to the retrieved chunk records, producing
   the display form:

   ```
   [1] cloud_computing_notes.md · 2. Virtualization  (score 0.7309)
   ```

**Where does it fit?**
`backend/app/rag/citations.py`, called from `backend/app/rag/pipeline.py`

**What if we removed it?**
The answer would still contain `[1]` markers and nothing to click. The markers would
become decoration — actively misleading, because they *look* like provenance.

---

### Fabricated citations are the worst failure mode

A missing citation is an honest gap. A **fabricated** citation is worse than no citation
at all, because it looks verifiable and is not.

This is why the resolver is adversarial rather than trusting:

- It never assumes a number is valid because the model produced it.
- Any number outside the retrieved set is flagged, not dropped silently.
- The pipeline turns a fabrication into a `CITATION_ERROR` grounding verdict
  ([07](07_grounding.md)) rather than a `PARTIALLY_SUPPORTED` one.
- The Security page includes a probe that feeds the resolver a citation to a
  non-existent excerpt and reports it being caught.

**How to demonstrate it**

```bash
# The probe, run from the Security page or directly
.venv/Scripts/python.exe -c "
from app.rag.citations import resolve_citations
excerpts = [{'number': 1, 'label': 'a.pdf p.1', 'content': 'Virtualization abstracts hardware.'}]
print(resolve_citations('Some claim [7].', excerpts).invalid_numbers)
"
# → [7]
```

---

### A bug worth knowing about

This one was real, and it is instructive.

Citations initially rendered as **"Document None" with score 0.0**. The cause:
the pipeline was passing `bundle.as_prompt_list()` into citation resolution. That method
returns only the fields the *prompt* needs — `number`, `label`, `content` — and drops the
provenance and the score.

So the resolver had the right numbers and nothing to resolve them to. The fix was to
build two views of the same bundle:

```python
prompt_excerpts   = bundle.as_prompt_list()          # what the model reads
evidence_excerpts = bundle.as_dict()["excerpts"]     # what the resolver needs
```

and use each for its own purpose.

**The lesson:** when two consumers need the same data in different shapes, name the
shapes explicitly. A single "get the excerpts" helper that silently returns different
things to different callers is how provenance disappears.

---

### Two more bugs, both about the *shape* of a marker

These were found by reading real API responses, not by reading code. Both were silent:
nothing raised, nothing logged, and the answer looked complete. They are worth studying
because the marker is a **contract between the model and the parser**, and a contract
with a sloppy format will eventually be broken.

#### Bug A — grouped markers `[2, 4]` were invisible

The original pattern was:

```python
CITATION_RE = re.compile(r"\[(\d{1,3})\]")   # matches [1] ... and NOT [2, 4]
```

A real answer contained:

> "...allows cloud providers to pool resources, sell them in small increments, and scale
> elastically **[2, 4]**."

Only citation `1` was resolved. **Citations 2 and 4 — the evidence for that sentence —
never existed.** The reader could not check the claim, which is the one thing citations
are for.

Worse, the same blind spot sat inside `validate_translation`. It compared the source's
markers against the translation's, so a translation that dropped an entire group still
passed. A check that cannot see half the data is not a check.

The fix makes one function the single authority on what a marker means:

```python
CITATION_RE = re.compile(r"\[\s*([1-9]\d{0,2}(?:\s*,\s*[1-9]\d{0,2})*)\s*\]")

def iter_citation_numbers(text):
    for match in CITATION_RE.finditer(strip_code(normalize_citation_markers(text))):
        for part in match.group(1).split(","):   # "[2, 4]" -> 2, 4
            if part.strip().isdigit():
                yield int(part.strip())
```

Citation resolution, grounding and the transform validators all now read markers through
this one function, so they cannot disagree. The `[1-9]` first digit is deliberate:
numbering is 1-based, so a prose reference like `arr[0]` no longer invents a citation.

#### Bug B — the Groq fallback cites with full-width brackets `【1】`

Gemini writes `[1]`. Groq wrote:

> "...improves how efficiently the hardware's CPU, memory and I/O are used**【1】**."

`【` and `】` are U+3010/U+3011 — *full-width CJK brackets*. Every regex expected ASCII, so:

```
citations: 0
grounding: PARTIALLY_SUPPORTED — "the answer did not include any [citations]"
```

The answer was properly cited and Carinaa reported that it cited **nothing**. Note the
compound failure: Gemini was rate-limiting (HTTP 429), so the fallback was answering —
meaning citations broke on exactly the runs where the system was already degraded.

The fix normalises the bracket vocabulary at the pipeline boundary, once, before citation
resolution:

```python
normalized_answer = normalize_citation_markers(result.answer)
```

`normalize_citation_markers` is a fixed translation table, **not** `unicodedata.normalize("NFKC")`.
That choice matters and is worth remembering:

- NFKC does **not** fold `【` (U+3010) — it would not have fixed the observed case at all.
- NFKC rewrites unrelated characters, which is unacceptable for text the user reads.

Because the normalisation happens before storage, the stored answer, its citation rows,
the RAG Trace, Read Aloud and the transform validators all agree on one form.

**Defence in depth.** The generation prompt now also asks for ASCII brackets explicitly.
Groq still ignored it on the next run — the log recorded
`Normalised non-ASCII citation brackets in the generated answer.` The lesson: *ask the
model for a format, but never depend on it. Validate at the boundary.*

**How to demonstrate it**

```bash
.venv/Scripts/python.exe -c "
from app.rag.citations import normalize_citation_markers, extract_citation_numbers
print(extract_citation_numbers('pooled【2, 4】'))   # [2, 4]  - grouped AND full-width
"
```

---

## Part 2 — The canonical answer

**What is it?**
One grounded answer per question, stored once and never modified.

**Why do we need it?**
Carinaa has four presentation features that transform an answer:

| Feature | What it does |
| --- | --- |
| **Translate** | Renders the answer in another language |
| **Shorten** | Compresses it to a shorter form |
| **Read Aloud** | Produces speakable text |
| **Explain** | Re-frames it for learning |

If any of these **overwrote** the answer, the system would lose its source of truth. Ask
for a Tamil translation, then ask for the original back — what do you get? A translation
of a translation, degrading each time. And the citations, which point at English source
text, would drift further from the evidence with every transform.

**How does it work?**
The canonical answer lives in `Message.content` and is **immutable**.

Every transform produces a separate `AnswerVariant` row:

```
messages.content                  ← the canonical grounded answer. Never changes.
answer_variants
    kind="translated", language="ta"      ← a Tamil rendering
    kind="shortened",  level="short"      ← a compressed form
    kind="explanation"                    ← a learning-mode explanation
```

Variants are cached, so switching language or condensing level the second time is
instant.

**Where does it fit?**
`backend/app/db/models.py` (`Message`, `AnswerVariant`),
`backend/app/features/`, `backend/app/api/routes_features.py`

**What if we removed it?**
Each transform would mutate the answer. Grounding verdicts would become meaningless
(they describe the canonical answer, not a translation). Citations would point at text
that no longer exists in the displayed answer. And quality would degrade monotonically
with each transform applied.

**How to demonstrate it**
Ask a question, translate to Tamil, then switch back to English. The original answer is
byte-for-byte identical, with the same citations. The E2E suite asserts exactly this:

```
[PASS] Translation does not modify the canonical answer
         original_unchanged=True
```

---

## Ordering: transforms happen last

This ordering is a rule, not a preference:

```
generate → ground → resolve citations → ANSWER (canonical)
                                          │
                                          ├─→ translate
                                          ├─→ shorten
                                          ├─→ read aloud
                                          └─→ explain
```

**Grounding and citation resolution must run on the canonical answer, before any
transform.** Otherwise the system would be grounding a translation — comparing Tamil
text against English evidence, which lexical overlap cannot do at all.

---

## Each transform, and its honest constraint

### Translate

**What:** Renders the canonical answer in English, Tamil, Malayalam, Telugu, Hindi,
Japanese or Italian.

**The rule:** citations are preserved exactly. `[1]` stays `[1]`, because it points at
the original source, which is not translated.

**Offline behaviour:** translation in Offline mode uses the local model. It does **not**
silently call an online translation API. If the local model cannot do it, the user is
told.

**Demonstrated by:** `[PASS] Translation preserves citations` in the E2E suite.

### Shorten

**What:** A **compression** operation, not a regeneration.

**Why that distinction matters:** asking a model to "summarise this" produces *new text*
that may drop a qualifier, invert a number, or lose a warning. Asking it to *compress*
existing text — under validation — is a different and safer operation.

**The constraint:** concepts, facts, numbers, warnings, citations and terminology must
survive. After shortening, the result is validated against the original.

**If validation fails, the original is kept** and the user is told why. It does not
return a shorter answer that silently lost a number.

**Demonstrated by** a real rejection in the E2E suite:

```
[PASS] Shortening rejected unsafe compression and kept the original
         HTTP 422 (rejected): "The result is 89% of the original length,
         which is not short enough for the 'Short' level..."
```

That is the system refusing to hand over a bad result. It is a better demo moment than
a success.

### Read Aloud

**What:** Speaks the answer using the browser's or device's built-in speech synthesis.

**Why browser-native:** it works **offline**, needs no API key, and sends no content to
an unknown service. For a product that handles private documents, that last point is not
a minor detail.

**Detail worth noticing:** citation markers are stripped from the spoken text, so the
voice does not read "bracket one". The displayed text keeps them.

**Demonstrated by:** `[PASS] Read Aloud strips citation markers so the voice does not
read them`.

### Explain

**What:** Re-frames the answer for someone learning the topic.

**Constraint:** it may add context and rephrase, but it must not introduce facts that
are not in the evidence. It is a *presentation* transform like the others, not a second
chance to answer.

---

## Next

→ [09 — Offline & Online Modes](09_offline_and_online_modes.md)
