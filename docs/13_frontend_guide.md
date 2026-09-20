# 13 — Frontend Guide

React + Vite + TypeScript + Tailwind. Ten screens, a full design-token system, and two
deliberately designed themes.

---

## The ten screens

| Screen | Route | Purpose |
| --- | --- | --- |
| **Landing** | `/` | What Carinaa is, and what makes it different |
| **Dashboard** | `/dashboard` | Workspace overview: documents, chunks, recent activity |
| **Knowledge Base** | `/knowledge-base` | Upload and manage documents; live ingestion progress |
| **Chat** | `/chat` | Ask questions; answers with citations and the response toolbar |
| **RAG Trace** | `/rag-trace` | Every stage of a query, with real durations |
| **Learning Mode** | `/learning-mode` | Seven RAG concepts, plus a live chunking lab |
| **Playground** | `/playground` | Retrieval-only tuning — top_k, candidate_k, re-ranking |
| **Document Viewer** | `/documents/:id` | The actual chunks that were stored, with provenance |
| **Analytics** | `/analytics` | Real metrics from recorded queries |
| **Settings** | `/settings` | Providers, retrieval, security, system |

---

## The screens that matter most for a demo

### RAG Trace — the whole pipeline, honestly

A waterfall of every stage: `query_analysis`, `query_embedding`, `vector_search`,
`candidate_retrieval`, `reranking`, `context_building`, `llm_generation`,
`citation_resolution`, `grounding`.

Three things make it trustworthy:

1. **Durations are measured**, not estimated. Generation dominates — tens of seconds on a
   CPU model, versus milliseconds for everything else.
2. **Skipped stages say "did not run"** rather than disappearing. If re-ranking is off,
   you see it marked skipped. Hiding it would imply it ran.
3. **No secrets, no prompts, no chain-of-thought.** Only measured facts.

The bar widths are proportional to real durations, which is why `llm_generation` visually
dwarfs everything else. That is the point — it makes the performance story obvious without
anyone having to explain it.

### Chat — the everyday chatbot

`/app/chat` is the primary surface and contains **no pipeline**. It has:

- the conversation,
- **Sources** — an expandable list of the documents used, each showing its page or
  section, with a quote button that reveals the exact retrieved passage,
- **Retrieval detail** — a collapsible panel with the real numbers: candidates
  returned, duplicates removed, below threshold, final excerpts, distance metric, and
  top and mean score.

The detailed pipeline is *not* here. It lives in Learning Mode, so a normal user is
never looking at a diagram they did not ask for.

One bug worth recording: the panel read `retrieval.candidates`, but
`RetrievalOutcome.as_dict()` emits `candidates_retrieved`, so the count was
permanently `0` on every answer. Both keys are now accepted.

### Learning Mode — chat with the pipeline beside it

Learning Mode is a **real route**, `/app/learning`, rendering the same `Chat`
component with a `learning` prop. Two columns: conversation on the left, the
pipeline on the right (60/40 on desktop). Below `lg` the column becomes a drawer
opened by **Show RAG Process**, so the pipeline can never cover the chat.

It is one component on purpose: a separate page with its own state would drift, and
a learner comparing Chat with Learning Mode would eventually be looking at two
different systems. Only the layout differs.

**Why it is a route and not a `?learning=1` flag.** React Router keeps `Chat`
mounted across a search-param change, so a flag read in a `useState` initializer is
never re-read. Clicking the nav item while already on Chat did nothing, and
`/app/learning` redirecting away made it look like a bounce back to Chat. That was
the "Learning Mode sometimes goes back to Chat" bug.

Each stage has two levels:

- **Level 1** — one beginner sentence, the duration, and a status icon.
- **Level 2** (expanded) — why the stage exists, what happens without it, a
  conceptual diagram, and every value the backend measured for that run.

The panel opens with *"What am I looking at?"* and an expandable *"What is RAG?"*, so
a beginner is oriented before any jargon appears. If a provider failed and the
fallback answered, that becomes a short lesson rather than a red error. The Live RAG
Trace sits at the bottom, collapsible, and clicking an event highlights its stage.

### RAG Laboratory — seven benches

`/app/playground` is the laboratory dashboard; `/app/playground/<lab>` is one bench.
Each bench runs **the same code the real pipeline runs** — a lab that reimplemented a
stage would teach the wrong thing, because what is being taught is what *this* system
does.

| Bench | Endpoint behind it |
| --- | --- |
| Chunking | `POST /api/documents/preview-chunks` (the real chunker) |
| Embeddings | `POST /api/labs/embed` |
| Vector store | `GET /api/documents/{id}/chunks` + `/chunk-stats` |
| Retrieval | `POST /api/chat/retrieve` |
| Context | `POST /api/chat/ask` → `response.context` |
| Generation | `POST /api/chat/ask` → answer, grounding, citations |
| Full pipeline | `POST /api/chat/ask` → `response.trace`, with step-through |

**The rule every bench follows:** if a stage is not enabled in the current
configuration, the bench says so and shows nothing in its place. The Chunking bench has
no strategy dropdown for exactly this reason — the chunker implements one strategy, and
a control that changed nothing would suggest strategy is a free choice here when it is
not.

Two benches are worth calling out:

- **Embeddings** plots the real 384-dimensional vectors through PCA onto two axes. The
  response reports how much variance those axes preserve (typically ~80% + ~19%), and
  the UI shows that number, because a projection that hides its own distortion teaches
  something false. The similarity matrix beside it is exact.
- **Full pipeline** has pause, previous and next, built for demonstrating at a lectern.

### Analytics — real numbers, with caveats

Every figure comes from recorded `query_logs` and `trace_events`. Where a metric is a
proxy rather than a measurement, the backend returns a `caveats` field and **the UI
renders it verbatim**, unedited. The UI does not soften a caveat.

Missing data renders as an **em dash (—)**, never as `0`. A zero is a claim; an em dash
is an admission. This distinction is enforced in `src/lib/format.ts`.

---

## Theming

Two themes, both **deliberately designed**. Neither is a fallback.

**How it works:** every Tailwind colour resolves to a CSS variable defined twice — once
in `:root` / `[data-theme="light"]`, once in `[data-theme="dark"]`.

```css
:root {
  --surface-1: 255 255 255;
  --text-1: 17 24 39;
}
[data-theme="dark"] {
  --surface-1: 17 20 28;
  --text-1: 226 232 240;   /* not pure white */
}
```

Tailwind consumes them with the alpha-value syntax:

```js
colors: { surface: { 1: "rgb(var(--surface-1) / <alpha-value>)" } }
```

So `bg-surface-1` and `bg-surface-1/50` both work, in both themes, with no `dark:`
variants scattered through the components.

**Two design decisions worth defending:**

1. **In dark mode, surfaces get *lighter* as they come forward** — the inverse of light
   mode. This is how depth reads on a dark background. A naive inversion of the light
   theme produces a muddy, flat interface.
2. **Dark-mode text is not pure white** (`#E2E8F0`, not `#FFFFFF`). Pure white on a dark
   background causes halation — letters appear to bleed — and is genuinely tiring to read.

**No flash of the wrong theme.** A small inline script in `index.html` reads the stored
preference and sets `data-theme` **before React mounts**. Without it, the browser paints
one frame of light theme and then flips to dark, which looks like a bug.

---

## State management

Four small context providers, composed in a deliberate order in `main.tsx`:

```
Theme → Auth → Workspace → Toast → Router
```

| Provider | Responsibility |
| --- | --- |
| `theme.tsx` | Choice (`light` / `dark` / `system`) vs **resolved** theme |
| `auth.tsx` | Token in localStorage, verified against `/auth/me` on mount; a 401 clears the session |
| `workspace.tsx` | The active workspace, persisted and re-validated on load |
| `toast.tsx` | Notifications — **errors and warnings do not auto-dismiss** |

That last one is deliberate. A success message can vanish after three seconds. An error
that vanishes is an error the user never read.

**The `theme.tsx` distinction matters:** "system" is a *choice*, and "dark" is a
*resolution* of that choice. Conflating them breaks the "follow my system setting" option
the moment the user's OS theme changes.

---

## Loading, empty and error states

Every screen handles four states, and they are designed rather than defaulted:

| State | Treatment |
| --- | --- |
| **Loading** | Skeletons that match the final layout, so nothing jumps when data arrives |
| **Empty** | An explanation of what would appear here and how to make it appear |
| **Error** | What went wrong, and what to do about it |
| **Success** | The data, with provenance where it exists |

The empty state for the Knowledge Base is not "No documents." It says what a document
would let you do and points at the upload button. An empty state is the first thing a new
user sees; it should teach, not just report absence.

---

## Accessibility

- **Keyboard navigable.** Tabs use arrow keys. Modals trap focus, close on Escape, and
  restore focus to the element that opened them.
- **Labelled inputs.** `aria-describedby` wired to help text and errors; errors use
  `aria-invalid`.
- **`prefers-reduced-motion` respected.** A global block disables animation and
  transitions when the user has asked for reduced motion. This is not optional
  politeness — for some users, motion causes real discomfort.
- **Visible focus rings** via `:focus-visible`, themed per theme.
- **Responsive** from mobile up, with a collapsing sidebar.

---

## Project layout

```
frontend/src/
  lib/          types.ts (mirrors the Pydantic schemas), api.ts, format.ts,
                speech.ts, cn.ts
  state/        theme, auth, workspace, toast
  hooks/        useAsync, usePolling, useMediaQuery
  components/
    ui/         Button, Card, Badge, Field, Feedback, Modal, Tabs
    brand/      Logo
    layout/     AppShell, WorkspaceSwitcher
    documents/  UploadDialog
    chat/       Citations, Markdown, AnswerToolbar, MessageBubble
    trace/      TraceTimeline
  pages/        the ten screens
```

**`lib/types.ts` is hand-written** to mirror the backend's Pydantic schemas. It is a
manual mirror, and that is a known trade-off: it can drift. The alternative — generating
types from the OpenAPI schema — is the right long-term answer and is noted as future
work.

---

## Build and run

```bash
cd frontend
npm install
npm run dev        # dev server on :5173, proxies /api to :8000
npm run build      # production build to dist/
npx tsc --noEmit   # typecheck
```

**One configuration detail that bites everyone:** the `@/*` path alias exists in
`tsconfig.json` for the type-checker, but **Vite and Rollup do not read tsconfig**. The
same alias must be declared in `vite.config.ts` under `resolve.alias`, or `tsc` passes
while the production build fails with `Rollup failed to resolve import "@/App"`.

**Single-origin deployment.** After `npm run build`, the backend detects `frontend/dist`
and serves it at `/`, so `http://127.0.0.1:8000` is the whole application with no dev
server. The static handler includes an SPA fallback: a request for `/dashboard` returns
`index.html` so React Router can render it, because no such file exists on disk. Without
that fallback, reloading any page other than the landing page would 404.

---

## Next

→ [14 — Evaluation](14_evaluation.md)
