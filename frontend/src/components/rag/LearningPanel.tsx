import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  EyeOff,
  Languages,
  Layers,
  Lightbulb,
  MessageSquare,
  MinusCircle,
  Network,
  RotateCcw,
  ScanSearch,
  Search,
  ShieldCheck,
  SkipForward,
  Sparkles,
  Target,
  Terminal,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api } from "@/lib/api";
import { Spinner } from "@/components/ui/Feedback";
import { formatDuration } from "@/lib/format";
import type { TraceStage } from "@/lib/types";
import { stageExplanation } from "./stageExplanations";
import { StageAnimation } from "./StageAnimation";
import { MessageDiagram } from "./MessageDiagram";
import { SimpleExplanation } from "./SimpleExplanation";

/**
 * The right-hand column of Learning Mode: "what Carinaa just did".
 *
 * THE MENTAL MODEL THIS PANEL TEACHES
 * -----------------------------------
 * A beginner should understand WHAT happened before they ever meet the words
 * "embedding", "re-ranking" or "grounding". So the panel is built in two levels:
 *
 *   LEVEL 1 (always visible)  six plain-language steps:
 *       Understand → Search → Find → Prepare → Generate → Verify
 *
 *   LEVEL 2 (expand a step)   the real technical stages that step is made of,
 *       with the values the backend actually measured: query analysis, query
 *       embedding, vector search, candidate retrieval, re-ranking, context
 *       building, LLM generation, citation resolution, grounding.
 *
 * Nothing is invented and nothing is removed: every technical stage of the real
 * trace is reachable exactly one click deeper than the simple story.
 *
 * EVERYTHING HERE IS PER MESSAGE. The panel is always explaining ONE answer -
 * the newest one, or whichever answer the user clicked. Selecting a different
 * answer replays THAT answer's recorded run from the start.
 */

const SKIP_KEYS = new Set(["label", "stage", "status", "seq", "trace_id"]);

function formatTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/**
 * The six beginner steps, and which technical stages each one is made of.
 *
 * The order matches the real pipeline; the mapping is many-to-one so a beginner
 * reads six ideas instead of nine stage names. Conditional stages (web search,
 * the offline failsafe) are filed under the step they belong to.
 */
interface SimpleStep {
  id: string;
  title: string;
  icon: LucideIcon;
  /** The one-sentence beginner explanation, visible without expanding. */
  simple: string;
  /** Technical stage names (trace `stage` values) that make up this step. */
  stages: string[];
}

const SIMPLE_STEPS: SimpleStep[] = [
  {
    id: "understand",
    title: "Understand",
    icon: ScanSearch,
    simple: "Carinaa reads your question and works out what you are asking.",
    stages: ["query_analysis"],
  },
  {
    id: "search",
    title: "Search",
    icon: Search,
    simple:
      "Carinaa searches the knowledge selected for this chat — by meaning, not just by matching words.",
    stages: ["query_embedding", "vector_search"],
  },
  {
    id: "find",
    title: "Find",
    icon: Target,
    simple:
      "Carinaa compares what it found and keeps the pieces most relevant to your question.",
    stages: ["candidate_retrieval", "reranking"],
  },
  {
    id: "prepare",
    title: "Prepare",
    icon: Layers,
    simple: "The useful passages are organised into notes for the AI to read.",
    stages: ["context_building"],
  },
  {
    id: "generate",
    title: "Generate",
    icon: Sparkles,
    simple: "The AI reads your question together with those notes and writes the answer.",
    stages: ["llm_generation", "web_search", "failsafe"],
  },
  {
    id: "verify",
    title: "Verify",
    icon: ShieldCheck,
    simple:
      "Carinaa checks that the answer is actually supported by the passages it found.",
    stages: ["citation_resolution", "grounding"],
  },
];

/**
 * Find a provider-failure-and-recovery pair.
 *
 * When the primary provider fails and the fallback answers, the trace contains two
 * `llm_generation` events: one with status=error and role=primary, then one with
 * status=ok and role=fallback. That is genuinely useful to a learner - it is a real
 * distributed-systems behaviour, happening live - so instead of a red "Error" it
 * becomes a short lesson in how fallbacks work.
 */
function findFallback(stages: TraceStage[]) {
  const failed = stages.find(
    (stage) => stage.status === "error" && stage.data?.role === "primary",
  );
  if (!failed) return null;
  const recovered = stages.find(
    (stage) => stage.status === "ok" && stage.data?.role === "fallback",
  );
  if (!recovered) return null;
  return {
    primary: String(failed.data?.provider ?? "the primary provider"),
    reason: String(failed.data?.error ?? "unavailable"),
    fallback: String(recovered.data?.provider ?? "the fallback provider"),
    model: recovered.data?.model ? String(recovered.data.model) : null,
  };
}

function entries(data: Record<string, unknown> | undefined) {
  if (!data) return [];
  return Object.entries(data)
    .filter(([key]) => !SKIP_KEYS.has(key))
    .map(([key, value]) => [
      key,
      typeof value === "object" && value !== null
        ? Array.isArray(value)
          ? value.length
            ? value.join(", ")
            : "none"
          : `${Object.keys(value).length} fields`
        : String(value),
    ] as [string, string]);
}

type StepStatus = "done" | "active" | "pending" | "failed" | "skipped";

export function LearningPanel({
  stages,
  totalMs,
  running,
  onClose,
  onPlayingChange,
  failed = false,
  failureMessage,
  question = "",
  selectedMessageId = null,
  allAnswers = [],
  onSelectAnswer,
  languages = [],
  className,
}: {
  stages: TraceStage[];
  totalMs?: number;
  running?: boolean;
  onClose?: () => void;
  /**
   * Reports whether the walkthrough is still playing. Chat holds a hard timeout
   * on the same state, so a failure here can never leave an answer hidden.
   */
  onPlayingChange?: (playing: boolean) => void;
  /**
   * Whether the run this panel is describing FAILED. When true the panel must
   * not present a completed pipeline: showing checkmarks next to an error asserts
   * a success the system did not achieve.
   */
  failed?: boolean;
  failureMessage?: string;
  /** The question the inspected answer belongs to (shown in "You asked"). */
  question?: string;
  /** id of the assistant answer being explained. */
  selectedMessageId?: number | null;
  /** Every inspectable answer, for the message picker. */
  allAnswers?: { messageId: number; question: string; answer: string }[];
  /** Choosing an answer in the picker / clicking a bubble. */
  onSelectAnswer?: (messageId: number) => void;
  /** Languages for this panel's own Translate control (includes Tanglish). */
  languages?: { code: string; name: string; native_name?: string }[];
  className?: string;
}) {
  /** Which simple step is expanded to its technical details. */
  const [openStep, setOpenStep] = useState<string | null>(null);
  const [showTrace, setShowTrace] = useState(false);
  // Only meaningful after a failure, where the previous run's stages are hidden
  // by default so they cannot be mistaken for this run's.
  const [showStages, setShowStages] = useState(false);

  /* ---- staged replay -------------------------------------------------------
     Walks the recorded run one stage at a time so the sequence is legible. The
     trace is already recorded, so this is a replay of a real run - and the UI
     says so. */
  const prefersReducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  const [revealed, setRevealed] = useState(0);
  const [playing, setPlaying] = useState(false);
  const runId = useRef(0);

  // A stable identity for "this is a different run", so the replay restarts for a
  // new question but not on every re-render. The inspected answer's id and its
  // trace are part of the key: switching to an older answer replays THAT trace
  // from the start, and asking a new question triggers the walkthrough
  // automatically - the user never has to start the replay by hand.
  const signature = useMemo(
    () =>
      `${selectedMessageId ?? ""}::` +
      stages.map((stage) => `${stage.seq}:${stage.stage}`).join("|"),
    [stages, selectedMessageId],
  );

  useEffect(() => {
    if (stages.length === 0) {
      setRevealed(0);
      setPlaying(false);
      return;
    }
    if (prefersReducedMotion) {
      setRevealed(stages.length);
      setPlaying(false);
      return;
    }
    runId.current += 1;
    setRevealed(0);
    setPlaying(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the run signature
  }, [signature, prefersReducedMotion]);

  // A failed run must not animate to completion.
  useEffect(() => {
    if (failed && playing) setPlaying(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only reacts to `failed`
  }, [failed]);

  useEffect(() => {
    onPlayingChange?.(playing);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- callback identity varies
  }, [playing]);

  useEffect(() => {
    if (!playing) return;
    if (revealed >= stages.length) {
      setPlaying(false);
      return;
    }
    const stage = stages[revealed];
    // Real duration drives the pace, clamped: a slow stage reads as slower than a
    // fast one, without a 23 s generation freezing the walkthrough.
    const raw = stage?.duration_ms ?? 0;
    const delay = Math.min(620, Math.max(150, raw * 0.35));
    const timer = window.setTimeout(() => setRevealed((value) => value + 1), delay);
    return () => window.clearTimeout(timer);
  }, [playing, revealed, stages]);

  const replay = () => {
    if (stages.length === 0) return;
    setRevealed(0);
    setPlaying(true);
  };


  /* ---- derived facts, all from the real trace ----------------------------- */

  const fallback = useMemo(() => findFallback(stages), [stages]);

  /** Conversational context: whether this question needed earlier turns. */
  const conversation = useMemo(() => {
    const stage = stages.find((item) => item.stage === "query_analysis");
    const data = stage?.data;
    if (!data?.is_followup) return null;
    return {
      original: String(data.original_question ?? ""),
      resolved: String(data.resolved_question ?? ""),
    };
  }, [stages]);

  /** What the model actually received, split by kind. */
  const contextUsed = useMemo(() => {
    const retrieval = stages.find((item) => item.stage === "candidate_retrieval");
    const building = stages.find((item) => item.stage === "context_building");
    const turns = Number(retrieval?.data?.conversation_turns_used ?? 0);
    const excerpts = Number(
      building?.data?.excerpts ?? building?.data?.excerpt_count ?? 0,
    );
    if (turns === 0 && excerpts === 0) return null;
    return { turns, excerpts };
  }, [stages]);

  /**
   * What retrieval was allowed to search, from the `candidate_retrieval` stage,
   * which the server annotates with the resolved scope. An answer is only
   * interpretable next to its scope.
   */
  const scope = useMemo(() => {
    const stage = stages.find((item) => item.stage === "candidate_retrieval");
    const data = stage?.data;
    if (!data?.retrieval_scope) return null;
    const names = Array.isArray(data.scope_file_names)
      ? (data.scope_file_names as unknown[]).map(String)
      : [];
    return {
      kind: String(data.retrieval_scope),
      searched: Number(data.workspace_documents_searched ?? 0),
      available: Number(data.workspace_documents_available ?? 0),
      note: data.scope_note ? String(data.scope_note) : "",
      files: names,
    };
  }, [stages]);

  /** The provider that actually wrote the answer (for Technical details). */
  const providerInfo = useMemo(() => {
    const generation = [...stages]
      .reverse()
      .find((item) => item.stage === "llm_generation" && item.status === "ok");
    if (!generation) return null;
    return {
      provider: String(generation.data?.provider ?? ""),
      model: generation.data?.model ? String(generation.data.model) : "",
      role: String(generation.data?.role ?? ""),
      usedFallback: Boolean(generation.data?.used_fallback),
      reason: generation.data?.fallback_reason
        ? String(generation.data.fallback_reason)
        : "",
    };
  }, [stages]);

  /** The passages retrieved for THIS answer - what the diagram is drawn from. */
  const retrievedSources = useMemo(() => {
    const stage = stages.find((item) => item.stage === "candidate_retrieval");
    const raw = stage?.data?.top_sources;
    if (!Array.isArray(raw))
      return [] as { label: string; document: string; section: string; score: number }[];
    return (raw as Record<string, unknown>[])
      .map((entry) => ({
        label: String(entry.label ?? ""),
        document: String(entry.document ?? ""),
        section: String(entry.section ?? ""),
        score: Number(entry.score ?? 0),
      }))
      .filter((entry) => entry.label || entry.document);
  }, [stages]);

  const hasTimes = stages.some((stage) => Boolean(stage.created_at));
  const hasAnyRun = stages.length > 0;


  /* ---- the six simple steps, with live status ------------------------------

     Status vocabulary (one icon per state, used consistently):
       ✓ done      the step finished
       ⏳ active   the step is being replayed right now
       ✗ failed    a technical stage in this step failed and nothing recovered it
       — skipped   the step was not needed for this question
       ○ pending   the replay has not reached this step yet
  */
  const steps = useMemo(() => {
    return SIMPLE_STEPS.map((step) => {
      const mine = step.stages
        .map((name) => ({
          name,
          // ALL trace events for this stage name, in order (llm_generation can
          // appear twice: primary failed, fallback answered).
          items: stages
            .map((stage, index) => ({ stage, index }))
            .filter(({ stage }) => stage.stage === name),
        }))
        .filter((entry) => entry.items.length > 0);

      const indexes = mine.flatMap((entry) => entry.items.map((item) => item.index));
      const revealedCount = indexes.filter((index) => index < revealed).length;
      const allRevealed = indexes.length > 0 && revealedCount === indexes.length;
      const partiallyRevealed = revealedCount > 0 && !allRevealed;
      const replayDone = !playing && revealed >= stages.length;

      const hasError = mine.some((entry) =>
        entry.items.some((item) => item.stage.status === "error"),
      );
      const hasOk = mine.some((entry) =>
        entry.items.some((item) => item.stage.status === "ok"),
      );
      const allSkipped =
        mine.length > 0 &&
        mine.every((entry) =>
          entry.items.every((item) => item.stage.status === "skipped"),
        );

      let status: StepStatus;
      if (mine.length === 0 || allSkipped) {
        // e.g. a step the question never needed (web search off, no re-ranking).
        status = "skipped";
      } else if (hasError && !hasOk) {
        status = "failed";
      } else if (allRevealed || replayDone) {
        status = "done";
      } else if (partiallyRevealed || (playing && indexes.some((i) => i === revealed))) {
        status = "active";
      } else {
        status = "pending";
      }

      const durationMs = mine
        .flatMap((entry) => entry.items)
        .reduce((sum, item) => sum + (item.stage.duration_ms || 0), 0);

      return { ...step, mine, status, durationMs };
    });
  }, [stages, revealed, playing]);

  /* ---- translate this explanation (opt-in, compact) ------------------------
     Translation is a choice, not a block that is always on screen. The control
     stays one compact row until the user asks for a language. */
  const [translateOpen, setTranslateOpen] = useState(false);
  const [translateLang, setTranslateLang] = useState("");
  const [translatedIntro, setTranslatedIntro] = useState("");
  const [translating, setTranslating] = useState(false);
  const [translateError, setTranslateError] = useState("");

  // A translation belongs to one run. Selecting another answer clears it, so the
  // panel can never show an explanation of a message the user is no longer reading.
  useEffect(() => {
    setTranslatedIntro("");
    setTranslateError("");
  }, [selectedMessageId]);

  const explanationSource = (() => {
    const searchedWhat = scope
      ? scope.kind === "chat"
        ? `the ${scope.searched} selected document(s)`
        : `all ${scope.available} document(s) in the workspace`
      : "the workspace";
    const lines = [
      `Question: ${question || "—"}`,
      "",
      `Carinaa answered this question with Retrieval-Augmented Generation. First it understood the ` +
        `question, then it searched ${searchedWhat} for passages that match the meaning of the question, ` +
        `kept the best ones, passed them to the AI as evidence, and asked the AI to answer only from ` +
        `that evidence. Finally it checked that every claim in the answer was actually supported by the ` +
        `passages it found, and attached the citations you can click.`,
    ];
    if (conversation) {
      lines.push(
        `This was a follow-up question: "${conversation.original}" was understood as "${conversation.resolved}" using the earlier conversation.`,
      );
    }
    if (contextUsed) {
      lines.push(
        `The AI received ${contextUsed.turns > 0 ? `${contextUsed.turns} earlier conversation turn(s) as context` : "no earlier conversation turns"} and ${contextUsed.excerpts} retrieved excerpt(s) as document evidence. Only the excerpts can be cited.`,
      );
    }
    return lines.join("\n");
  })();


  return (
    <div className={cn("flex h-full min-h-0 flex-col bg-surface", className)}>
      {/* ---- header ------------------------------------------------------ */}
      <div className="border-b border-line px-4 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="flex items-center gap-1.5 text-sm font-semibold text-ink">
              <span aria-hidden>🔬</span> How Carinaa answered
            </h2>
            <p className="mt-0.5 text-2xs leading-relaxed text-muted">
              A replay of the exact steps behind one answer — plain words first,
              technical details one click deeper.
            </p>
          </div>
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-line px-2 py-1 text-2xs text-muted transition hover:border-line-strong hover:text-ink"
            >
              <EyeOff className="h-3 w-3" />
              Hide
            </button>
          ) : null}
        </div>
        {totalMs ? (
          <p className="mt-2 font-mono text-2xs text-faint">
            {stages.length} stages · {formatDuration(totalMs)} total
          </p>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin px-4 py-4">
        {/* ---- you asked: the panel always belongs to ONE question --------- */}
        <div className="mb-4 rounded-xl border border-brand/25 bg-brand/6 p-3.5">
          <p className="text-[0.625rem] font-semibold uppercase tracking-wider text-brand">
            You asked
          </p>
          <p className="mt-1 text-xs font-medium leading-relaxed text-ink">
            {question ? (
              <>“{question}”</>
            ) : running ? (
              "Answering a new question…"
            ) : (
              "Ask a question to see how Carinaa answers it."
            )}
          </p>
          {allAnswers.length > 1 ? (
            <label className="mt-2 block">
              <span className="sr-only">Choose an answer to inspect</span>
              <select
                value={selectedMessageId ?? ""}
                onChange={(event) => {
                  const id = Number(event.target.value);
                  if (id > 0) onSelectAnswer?.(id);
                }}
                className="w-full rounded-lg border border-line-strong bg-surface px-2 py-1 text-2xs text-ink"
              >
                {allAnswers.map((item, index) => (
                  <option key={item.messageId} value={item.messageId}>
                    #{index + 1} — {item.question || "answer"}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <p className="mt-1.5 text-[0.625rem] leading-relaxed text-faint">
            Every answer has its own steps. Click any answer in the chat to inspect it.
          </p>
        </div>

        {/* ---- first visit: the story before any jargon ------------------ */}
        {!hasAnyRun && !running ? (
          <div className="mb-4 rounded-xl border border-line bg-sunken p-3.5">
            <p className="flex items-center gap-1.5 text-2xs font-medium text-ink">
              <Lightbulb className="h-3.5 w-3.5 text-brand" />
              What happens when you ask something
            </p>
            <ol className="mt-2 list-inside list-decimal space-y-1 text-2xs leading-relaxed text-muted">
              <li>Carinaa understands your question.</li>
              <li>It searches your documents for useful information.</li>
              <li>It keeps the most relevant pieces.</li>
              <li>It gives those pieces to the AI.</li>
              <li>The AI writes the answer — and Carinaa checks it.</li>
            </ol>
            <p className="mt-2 text-2xs leading-relaxed text-faint">
              Ask a question in the chat and watch those steps happen here, with the
              real measurements from your question.
            </p>
            <details className="mt-2">
              <summary className="cursor-pointer text-2xs font-medium text-brand">
                What is RAG?
              </summary>
              <p className="mt-1.5 text-2xs leading-relaxed text-muted">
                RAG stands for <strong className="text-ink">Retrieval-Augmented Generation</strong>.
                Instead of answering only from what an AI already knows, Carinaa first
                looks through your documents for useful information, gives that
                information to the AI, and only then writes the answer. That is why the
                answer can point back to the exact passages it used.
              </p>
            </details>
          </div>
        ) : null}

        {running && stages.length === 0 ? (
          <p className="mb-4 flex items-center gap-2 text-2xs text-muted">
            <Spinner size={11} />
            Working through the steps…
          </p>
        ) : null}


        {/* ---- failure: say where Carinaa stopped, do not fake success ----- */}
        {failed ? (
          <div
            role="alert"
            className="mb-4 rounded-xl border border-negative/35 bg-negative/8 p-4"
          >
            <p className="flex items-center gap-1.5 text-xs font-medium text-negative">
              <XCircle className="h-3.5 w-3.5" />
              Carinaa stopped before producing an answer
            </p>
            <p className="mt-1.5 text-2xs leading-relaxed text-muted">
              {failureMessage ||
                "The request failed, so there is no pipeline to show for it."}
            </p>
            <p className="mt-2 border-t border-negative/20 pt-2 text-2xs leading-relaxed text-faint">
              This is also how the pipeline communicates errors: a step that fails is
              marked ✗, steps that never ran stay at —, and Carinaa will not show a
              completed pipeline for a run that produced no answer.
            </p>
            {stages.length > 0 ? (
              <button
                type="button"
                onClick={() => setShowStages((value) => !value)}
                aria-expanded={showStages}
                className="mt-2 text-2xs text-faint underline decoration-dotted underline-offset-2 transition hover:text-ink"
              >
                {showStages ? "Hide" : "Show"} the previous run&apos;s steps for reference
              </button>
            ) : null}
          </div>
        ) : null}

        {/* ---- provider fallback, taught as a lesson ---------------------- */}
        {fallback ? (
          <div className="mb-4 animate-fade-up rounded-xl border border-caution/30 bg-caution/8 p-3.5">
            <p className="flex items-center gap-1.5 text-2xs font-medium text-caution">
              <AlertTriangle className="h-3.5 w-3.5" />
              The main AI provider was unavailable
            </p>
            <p className="mt-1.5 text-2xs leading-relaxed text-muted">
              {fallback.primary} failed ({fallback.reason}), so Carinaa automatically
              switched to a backup provider and still answered. The answer is labelled
              with the provider that actually wrote it.
            </p>
            <ol className="mt-2.5 space-y-1">
              <li className="flex items-center gap-2 rounded-lg border border-line bg-surface px-2 py-1.5">
                <span className="text-2xs text-ink">{fallback.primary}</span>
                <span className="ml-auto text-2xs text-faint">primary</span>
              </li>
              <li className="flex items-center gap-2 pl-2 text-2xs text-caution">
                <span aria-hidden>↓</span>
                <AlertTriangle className="h-3 w-3" />
                {fallback.reason}
              </li>
              <li className="flex items-center gap-2 rounded-lg border border-line bg-surface px-2 py-1.5">
                <span className="text-2xs text-ink">{fallback.fallback}</span>
                <span className="ml-auto text-2xs text-faint">fallback</span>
              </li>
              <li className="flex items-center gap-2 pl-2 text-2xs text-positive">
                <span aria-hidden>↓</span>
                <Check className="h-3 w-3" />
                Response generated
              </li>
            </ol>
          </div>
        ) : null}

        {/* ---- what happened, in plain words ------------------------------ */}
        {hasAnyRun && (failed ? showStages : true) ? (
          <div className="mb-4 rounded-xl border border-line bg-surface p-3.5">
            <p className="flex items-center gap-1.5 text-2xs font-medium text-ink">
              <Lightbulb className="h-3.5 w-3.5 text-brand" />
              What happened, in plain words
            </p>
            <div className="mt-2">
              <SimpleExplanation
                stages={stages}
                question={question}
                scope={scope}
                context={contextUsed}
              />
            </div>
          </div>
        ) : null}


        {/* ---- the pipeline: six plain-language steps ---------------------- */}
        {hasAnyRun && (failed ? showStages : true) ? (
          <div className="rounded-xl border border-line bg-surface">
            <div className="flex items-center gap-2 border-b border-line px-3 py-2.5">
              <span aria-hidden className="text-xs">🔄</span>
              <span className="min-w-0 flex-1 text-2xs font-medium text-ink">
                The steps behind this answer
              </span>
              {playing ? (
                <>
                  <span className="flex items-center gap-1.5 text-2xs text-muted">
                    <Spinner size={11} />
                    Replaying…
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      setPlaying(false);
                      setRevealed(stages.length);
                    }}
                    className="inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-2xs text-muted transition hover:border-line-strong hover:text-ink"
                  >
                    <SkipForward className="h-3 w-3" />
                    Skip
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={replay}
                  className="inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-2xs text-muted transition hover:border-line-strong hover:text-ink"
                >
                  <RotateCcw className="h-3 w-3" />
                  Replay
                </button>
              )}
            </div>

            <ol className="divide-y divide-line">
              {steps.map((step) => {
                const isOpen = openStep === step.id;
                const Icon = step.icon;
                return (
                  <li key={step.id}>
                    <button
                      type="button"
                      onClick={() => setOpenStep(isOpen ? null : step.id)}
                      aria-expanded={isOpen}
                      className={cn(
                        "flex w-full items-start gap-2.5 px-3 py-2.5 text-left transition",
                        step.status === "active" && "bg-brand/6",
                        step.status === "pending" && "opacity-45",
                      )}
                    >
                      <StepStatusIcon status={step.status} />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-baseline justify-between gap-2">
                          <span
                            className={cn(
                              "text-2xs font-semibold",
                              step.status === "skipped" ? "text-faint" : "text-ink",
                            )}
                          >
                            <Icon className="mr-1 inline h-3 w-3 align-[-2px]" />
                            {step.title}
                          </span>
                          <span className="shrink-0 font-mono text-2xs text-faint">
                            {step.status === "skipped" || step.status === "pending"
                              ? "—"
                              : formatDuration(step.durationMs)}
                          </span>
                        </span>
                        <span className="mt-0.5 block text-2xs leading-relaxed text-muted">
                          {step.status === "skipped"
                            ? "Not needed for this question."
                            : step.status === "failed"
                              ? "This step failed — open it to see why."
                              : step.simple}
                        </span>
                        {isOpen ? (
                          <span className="mt-1 block text-[0.625rem] font-medium text-brand">
                            Hide technical details
                          </span>
                        ) : (
                          <span className="mt-1 block text-[0.625rem] text-faint">
                            Technical details ▸
                          </span>
                        )}
                      </span>
                      {isOpen ? (
                        <ChevronDown className="mt-0.5 h-3 w-3 shrink-0 text-faint" />
                      ) : (
                        <ChevronRight className="mt-0.5 h-3 w-3 shrink-0 text-faint" />
                      )}
                    </button>


                    {/* Level 2: the real technical stages, with measurements. */}
                    {isOpen ? (
                      <div className="animate-fade-up space-y-2.5 border-t border-line bg-sunken/50 px-3 py-3">
                        {step.mine.length === 0 ? (
                          <p className="text-2xs text-faint">
                            This step did not run for this question.
                          </p>
                        ) : (
                          step.mine.map((entry) => {
                            const explanation = stageExplanation(entry.name);
                            return (
                              <div
                                key={entry.name}
                                className="rounded-lg border border-line bg-surface p-2.5"
                              >
                                {entry.items.map(({ stage }) => (
                                  <div
                                    key={`${stage.seq}-${stage.stage}`}
                                    className="mb-1.5 flex items-center gap-2"
                                  >
                                    {stage.status === "error" ? (
                                      <XCircle className="h-3 w-3 shrink-0 text-negative" />
                                    ) : stage.status === "skipped" ? (
                                      <MinusCircle className="h-3 w-3 shrink-0 text-faint" />
                                    ) : (
                                      <Check className="h-3 w-3 shrink-0 text-positive" />
                                    )}
                                    <span
                                      className={cn(
                                        "min-w-0 flex-1 text-2xs font-medium",
                                        stage.status === "skipped" ? "text-faint" : "text-ink",
                                      )}
                                    >
                                      {stage.label || explanation?.label || stage.stage}
                                    </span>
                                    <span className="shrink-0 font-mono text-2xs text-faint">
                                      {stage.status === "skipped"
                                        ? "—"
                                        : formatDuration(stage.duration_ms)}
                                    </span>
                                  </div>
                                ))}

                                {explanation ? (
                                  <dl className="space-y-1.5 text-2xs leading-relaxed">
                                    <div>
                                      <dt className="font-medium text-ink">What happened here</dt>
                                      <dd className="text-muted">{explanation.what}</dd>
                                    </div>
                                    <div>
                                      <dt className="font-medium text-ink">Why it exists</dt>
                                      <dd className="text-muted">{explanation.why}</dd>
                                    </div>
                                  </dl>
                                ) : null}

                                <StageAnimation stage={entry.name} question={question} />

                                {entry.items.map(({ stage }) => {
                                  const details = entries(stage.data);
                                  if (details.length === 0) return null;
                                  return (
                                    <details key={`data-${stage.seq}-${stage.stage}`} className="mt-1.5">
                                      <summary className="cursor-pointer text-2xs font-medium text-ink">
                                        Measured in this run
                                      </summary>
                                      <dl className="mt-1.5 space-y-0.5">
                                        {details.map(([key, value]) => (
                                          <div key={key} className="flex gap-2 text-2xs">
                                            <dt className="shrink-0 text-faint">
                                              {key.replace(/_/g, " ")}
                                            </dt>
                                            <dd className="min-w-0 flex-1 break-words font-mono text-ink">
                                              {value}
                                            </dd>
                                          </div>
                                        ))}
                                      </dl>
                                    </details>
                                  );
                                })}
                              </div>
                            );
                          })
                        )}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          </div>
        ) : null}


        {/* ---- knowledge used ------------------------------------------- */}
        {hasAnyRun && (scope || contextUsed) && (failed ? showStages : true) ? (
          <div className="mt-4 rounded-xl border border-line bg-surface p-3.5">
            <p className="flex items-center gap-1.5 text-2xs font-medium text-ink">
              <BookOpen className="h-3.5 w-3.5 text-muted" />
              Knowledge used for this answer
            </p>
            {scope ? (
              <p className="mt-1.5 text-2xs leading-relaxed text-muted">
                {scope.kind === "chat" ? (
                  <>
                    Searched only the{" "}
                    <strong className="text-ink">
                      {scope.searched} document{scope.searched === 1 ? "" : "s"}
                    </strong>{" "}
                    selected for this chat (of {scope.available} in the workspace).
                  </>
                ) : (
                  <>
                    Searched the whole workspace —{" "}
                    <strong className="text-ink">
                      all {scope.available} document{scope.available === 1 ? "" : "s"}
                    </strong>{" "}
                    — because no selection was made for this chat.
                  </>
                )}
              </p>
            ) : null}
            {scope && scope.files.length > 0 ? (
              <ul className="mt-1.5 flex flex-wrap gap-1">
                {scope.files.map((name) => (
                  <li
                    key={name}
                    className="rounded border border-line bg-sunken px-1.5 py-0.5 text-[0.625rem] text-muted"
                  >
                    {name}
                  </li>
                ))}
              </ul>
            ) : null}
            {contextUsed ? (
              <p className="mt-2 border-t border-line pt-2 text-2xs leading-relaxed text-faint">
                The AI read{" "}
                <strong className="text-muted">
                  {contextUsed.excerpts} passage{contextUsed.excerpts === 1 ? "" : "s"}
                </strong>{" "}
                from your documents
                {contextUsed.turns > 0 ? (
                  <>
                    {" "}plus{" "}
                    <strong className="text-muted">
                      {contextUsed.turns} earlier message{contextUsed.turns === 1 ? "" : "s"}
                    </strong>{" "}
                    from this conversation. Earlier messages are context, not evidence —
                    they are never cited as sources.
                  </>
                ) : (
                  ". No earlier messages were needed."
                )}
              </p>
            ) : null}
          </div>
        ) : null}

        {/* ---- conversation context: memory is not evidence ---------------- */}
        {conversation ? (
          <div className="mt-4 rounded-xl border border-brand/25 bg-brand/6 p-3.5">
            <p className="flex items-center gap-1.5 text-2xs font-medium text-ink">
              <MessageSquare className="h-3.5 w-3.5 text-brand" />
              This was a follow-up question
            </p>
            <p className="mt-1.5 text-2xs leading-relaxed text-muted">
              “{conversation.original}” only makes sense with the earlier conversation,
              so Carinaa read it as “{conversation.resolved}”. Conversation history is
              never treated as document evidence.
            </p>
          </div>
        ) : null}

        {/* ---- conceptual diagram from THIS message's real sources -------- */}
        {(question || retrievedSources.length > 0) && hasAnyRun ? (
          <div className="mt-4 rounded-xl border border-line bg-surface">
            <div className="flex w-full items-center gap-2 px-3 py-2.5">
              <Network className="h-3.5 w-3.5 text-muted" />
              <span className="flex-1 text-2xs font-medium text-ink">
                Conceptual view
              </span>
              <span className="text-[0.625rem] text-faint">
                for “{question.slice(0, 28) || "this question"}”
              </span>
            </div>
            <div className="border-t border-line px-3 py-3">
              <MessageDiagram question={question} sources={retrievedSources} />
            </div>
          </div>
        ) : null}


        {/* ---- translate this explanation: opt-in and compact ------------- */}
        {hasAnyRun ? (
          <div className="mt-4 rounded-xl border border-line bg-surface">
            <button
              type="button"
              onClick={() => setTranslateOpen((value) => !value)}
              aria-expanded={translateOpen}
              className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
            >
              <Languages className="h-3.5 w-3.5 text-muted" />
              <span className="flex-1 text-2xs font-medium text-ink">
                Translate this explanation
              </span>
              {translateOpen ? (
                <ChevronDown className="h-3 w-3 text-faint" />
              ) : (
                <ChevronRight className="h-3 w-3 text-faint" />
              )}
            </button>
            {translateOpen ? (
              <div className="animate-fade-up border-t border-line px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={translateLang}
                    onChange={(event) => setTranslateLang(event.target.value)}
                    className="rounded-lg border border-line-strong bg-surface px-1.5 py-1 text-2xs text-ink"
                    aria-label="Target language"
                  >
                    <option value="">language…</option>
                    {languages
                      .filter((l) => l.code !== "en")
                      .map((l) => (
                        <option key={l.code} value={l.code}>
                          {l.name}
                        </option>
                      ))}
                  </select>
                  <button
                    type="button"
                    disabled={!translateLang || translating}
                    onClick={() => {
                      setTranslateError("");
                      setTranslating(true);
                      api.features
                        .translateText(explanationSource, translateLang)
                        .then((result) => setTranslatedIntro(result.content))
                        .catch((cause) =>
                          setTranslateError(
                            cause instanceof ApiError ? cause.message : "Translation failed.",
                          ),
                        )
                        .finally(() => setTranslating(false));
                    }}
                    className="inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-2xs text-muted transition hover:border-line-strong hover:text-ink disabled:opacity-50"
                  >
                    {translating ? <Spinner size={11} /> : <Check className="h-3 w-3" />}
                    Translate
                  </button>
                  {translatedIntro ? (
                    <button
                      type="button"
                      onClick={() => setTranslatedIntro("")}
                      className="text-2xs text-faint underline decoration-dotted transition hover:text-ink"
                    >
                      show English
                    </button>
                  ) : null}
                </div>
                {translateError ? (
                  <p className="mt-2 text-2xs text-negative">{translateError}</p>
                ) : null}
                {translatedIntro ? (
                  <div className="mt-2">
                    <p className="whitespace-pre-wrap text-2xs leading-relaxed text-ink">
                      {translatedIntro}
                    </p>
                    <p className="mt-1.5 text-[0.625rem] text-faint">
                      Machine translation of the explanation above. The answer and its
                      citations are unchanged.
                    </p>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}


        {/* ---- technical details: provider, timings, the raw trace --------- */}
        {hasAnyRun ? (
          <div className="mt-4 rounded-xl border border-line">
            <button
              type="button"
              onClick={() => setShowTrace((value) => !value)}
              aria-expanded={showTrace}
              className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
            >
              <Terminal className="h-3.5 w-3.5 text-muted" />
              <span className="flex-1 text-2xs font-medium text-ink">
                Technical details &amp; raw trace
              </span>
              {showTrace ? (
                <ChevronDown className="h-3 w-3 text-faint" />
              ) : (
                <ChevronRight className="h-3 w-3 text-faint" />
              )}
            </button>

            {showTrace ? (
              <div className="animate-fade-up space-y-3 border-t border-line px-3 py-2.5">
                {providerInfo ? (
                  <dl className="space-y-1 text-2xs">
                    <div className="flex gap-2">
                      <dt className="shrink-0 text-faint">LLM provider</dt>
                      <dd className="min-w-0 flex-1 font-medium text-ink">
                        {providerInfo.provider
                          ? providerInfo.provider.charAt(0).toUpperCase() +
                            providerInfo.provider.slice(1)
                          : "—"}
                        {providerInfo.usedFallback ? " (fallback)" : ""}
                      </dd>
                    </div>
                    {providerInfo.model ? (
                      <div className="flex gap-2">
                        <dt className="shrink-0 text-faint">Model</dt>
                        <dd className="min-w-0 flex-1 font-mono text-ink">
                          {providerInfo.model}
                        </dd>
                      </div>
                    ) : null}
                    {providerInfo.reason ? (
                      <div className="flex gap-2">
                        <dt className="shrink-0 text-faint">Fallback reason</dt>
                        <dd className="min-w-0 flex-1 break-words text-muted">
                          {providerInfo.reason}
                        </dd>
                      </div>
                    ) : null}
                    {totalMs ? (
                      <div className="flex gap-2">
                        <dt className="shrink-0 text-faint">Total time</dt>
                        <dd className="min-w-0 flex-1 font-mono text-ink">
                          {formatDuration(totalMs)}
                        </dd>
                      </div>
                    ) : null}
                  </dl>
                ) : null}

                <div>
                  <p className="mb-2 text-2xs leading-relaxed text-faint">
                    The same run as a raw log — every event the backend recorded.
                  </p>
                  <ul className="space-y-0.5 font-mono text-2xs">
                    {stages.map((stage) => {
                      const time = formatTime(stage.created_at);
                      return (
                        <li
                          key={`trace-${stage.seq}-${stage.stage}`}
                          className="flex items-baseline gap-2 rounded px-1 py-0.5"
                        >
                          {hasTimes ? (
                            <span className="w-16 shrink-0 text-faint">{time ?? "—"}</span>
                          ) : null}
                          <span
                            className={cn(
                              "min-w-0 flex-1 truncate",
                              stage.status === "error"
                                ? "text-negative"
                                : stage.status === "skipped"
                                  ? "text-faint"
                                  : "text-ink",
                            )}
                          >
                            {stage.label || stage.stage}
                          </span>
                          <span className="shrink-0 text-faint">
                            {stage.status === "skipped"
                              ? "—"
                              : formatDuration(stage.duration_ms)}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** One icon per step state, so the vocabulary stays consistent. */
function StepStatusIcon({ status }: { status: StepStatus }) {
  if (status === "active") {
    return (
      <span className="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center">
        <span className="h-2 w-2 animate-pulse rounded-full bg-brand" />
      </span>
    );
  }
  if (status === "failed") {
    return <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-negative" />;
  }
  if (status === "skipped") {
    return <MinusCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-faint" />;
  }
  if (status === "pending") {
    return (
      <span className="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center">
        <span className="h-1.5 w-1.5 rounded-full bg-line-strong" />
      </span>
    );
  }
  return <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-positive" />;
}

export default LearningPanel;
