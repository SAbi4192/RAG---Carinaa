import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  Lightbulb,
  MinusCircle,
  RotateCcw,
  SkipForward,
  Terminal,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { Spinner } from "@/components/ui/Feedback";
import { formatDuration } from "@/lib/format";
import type { TraceStage } from "@/lib/types";
import { stageExplanation } from "./stageExplanations";
import { StageAnimation } from "./StageAnimation";

/**
 * The right-hand column of Learning Mode: "what Carinaa just did".
 *
 * DESIGN PRINCIPLE (the brief's §31)
 * ---------------------------------
 * Simple explanation → animation → real metrics → technical details.
 *
 * A beginner reads the top of each stage and understands it. An advanced reader
 * expands and gets every measured value the backend recorded. Both are reading the
 * same trace; neither view invents anything.
 *
 * The pipeline is deliberately the SECONDARY surface (§36). It sits beside the
 * conversation, never over it, and it can be hidden entirely.
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
 * Find a provider-failure-and-recovery pair.
 *
 * When the primary provider fails and the fallback answers, the trace contains two
 * `llm_generation` events: one with status=error and role=primary, then one with
 * status=ok and role=fallback. That is genuinely useful to a learner - it is a real
 * distributed-systems behaviour, happening live - so instead of a red "Error" it
 * becomes a short lesson in how fallbacks work (§25).
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

export function LearningPanel({
  stages,
  totalMs,
  running,
  onClose,
  className,
}: {
  stages: TraceStage[];
  totalMs?: number;
  running?: boolean;
  onClose?: () => void;
  className?: string;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [highlighted, setHighlighted] = useState<string | null>(null);

  /* ---- staged reveal (brief 18-22) ---------------------------------------
     Walks the stages one at a time so the sequence is legible: this stage is
     running, these finished, those have not started. The trace is already
     recorded, so this is a replay of a real run - and the UI says so. */
  const prefersReducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  const [revealed, setRevealed] = useState(0);
  const [playing, setPlaying] = useState(false);
  const runId = useRef(0);

  // A stable identity for "this is a different run", so the reveal restarts for a
  // new question but not on every re-render.
  const signature = useMemo(
    () => stages.map((stage) => `${stage.seq}:${stage.stage}`).join("|"),
    [stages],
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

  const stageState = (index: number): "done" | "active" | "pending" => {
    if (index < revealed) return "done";
    if (index === revealed && playing) return "active";
    if (!playing && revealed >= stages.length) return "done";
    return "pending";
  };

  const [showTrace, setShowTrace] = useState(false);
  const [showIntro, setShowIntro] = useState(true);

  const fallback = useMemo(() => findFallback(stages), [stages]);

  /**
   * What retrieval was allowed to search.
   *
   * Read from the `candidate_retrieval` stage, which the server annotates with the
   * resolved scope. Shown because an answer is only interpretable next to its
   * scope: "5 excerpts" means something different when 2 documents were searched
   * than when 12 were.
   */
  const scope = useMemo(() => {
    const stage = stages.find((item) => item.stage === "candidate_retrieval");
    const data = stage?.data;
    if (!data?.retrieval_scope) return null;
    return {
      kind: String(data.retrieval_scope),
      searched: Number(data.workspace_documents_searched ?? 0),
      available: Number(data.workspace_documents_available ?? 0),
      note: data.scope_note ? String(data.scope_note) : "",
    };
  }, [stages]);
  const hasTimes = stages.some((stage) => Boolean(stage.created_at));

  return (
    <div className={cn("flex h-full min-h-0 flex-col bg-surface", className)}>
      {/* ---- header (§18) ------------------------------------------- */}
      <div className="border-b border-line px-4 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="flex items-center gap-1.5 text-sm font-semibold text-ink">
              <span aria-hidden>🔬</span> RAG Learning
            </h2>
            <p className="mt-0.5 text-2xs leading-relaxed text-muted">
              Watch how Carinaa finds information and builds your answer.
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
        {/* ---- beginner context (§29, §30) -------------------------- */}
        {showIntro ? (
          <div className="mb-4 animate-fade-up rounded-xl border border-brand/25 bg-brand/6 p-3.5">
            <div className="flex items-start justify-between gap-2">
              <p className="flex items-center gap-1.5 text-2xs font-medium text-ink">
                <Lightbulb className="h-3.5 w-3.5 text-brand" />
                What am I looking at?
              </p>
              <button
                type="button"
                onClick={() => setShowIntro(false)}
                className="text-2xs text-faint transition hover:text-ink"
              >
                dismiss
              </button>
            </div>
            <p className="mt-1.5 text-2xs leading-relaxed text-muted">
              You are watching Carinaa answer your question using{" "}
              <strong className="text-ink">Retrieval-Augmented Generation</strong>. Instead of
              answering from memory alone, Carinaa first searches your documents, hands the
              relevant pieces to the AI, and generates an answer from that.
            </p>
            <details className="mt-2">
              <summary className="cursor-pointer text-2xs font-medium text-brand">
                What is RAG?
              </summary>
              <ol className="mt-1.5 list-inside list-decimal space-y-0.5 text-2xs leading-relaxed text-muted">
                <li>You ask a question.</li>
                <li>Carinaa searches your documents.</li>
                <li>It finds relevant pieces of information.</li>
                <li>Those pieces are given to the AI.</li>
                <li>The AI uses them to write the answer.</li>
              </ol>
              <p className="mt-2 text-2xs leading-relaxed text-faint">
                Click any stage below to see what it means and what it measured.
              </p>
            </details>
          </div>
        ) : null}

        {running && stages.length === 0 ? (
          <p className="text-2xs text-faint">Working through the pipeline…</p>
        ) : null}

        {/* ---- retrieval scope (§42, §43) --------------------------- */}
        {scope ? (
          <div className="mb-4 rounded-xl border border-line bg-sunken p-3.5">
            <p className="flex items-center gap-1.5 text-2xs font-medium text-ink">
              <BookOpen className="h-3.5 w-3.5 text-muted" />
              Retrieval scope
            </p>
            <p className="mt-1.5 text-2xs leading-relaxed text-muted">
              {scope.kind === "chat" ? (
                <>
                  This question searched{" "}
                  <strong className="text-ink">
                    {scope.searched} of {scope.available}
                  </strong>{" "}
                  documents — only the ones selected for this chat.
                </>
              ) : (
                <>
                  This question searched{" "}
                  <strong className="text-ink">all {scope.available}</strong> document
                  {scope.available === 1 ? "" : "s"} in the workspace, because no chat
                  scope was set.
                </>
              )}
            </p>
            {scope.note ? (
              <p className="mt-1 text-2xs leading-relaxed text-faint">{scope.note}</p>
            ) : null}
          </div>
        ) : null}

        {/* ---- fallback lesson (§25) -------------------------------- */}
        {fallback ? (
          <div className="mb-4 animate-fade-up rounded-xl border border-caution/30 bg-caution/8 p-3.5">
            <p className="flex items-center gap-1.5 text-2xs font-medium text-caution">
              <AlertTriangle className="h-3.5 w-3.5" />
              Primary AI provider unavailable
            </p>
            <p className="mt-1.5 text-2xs leading-relaxed text-caution/90">
              {fallback.primary} {fallback.reason}. Carinaa automatically switched to its
              fallback provider.
            </p>

            {/* The chain, drawn. A failure that shows its recovery path teaches
                something; a failure that just says "error" teaches nothing. */}
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

            <p className="mt-2 flex items-center gap-1.5 text-2xs font-medium text-positive">
              <Check className="h-3.5 w-3.5" />
              {fallback.fallback} generated the response
            </p>
            <details className="mt-2">
              <summary className="cursor-pointer text-2xs text-caution/80">
                Technical details
              </summary>
              <dl className="mt-1 space-y-0.5 font-mono text-2xs text-caution/80">
                <div>error: {fallback.reason}</div>
                <div>provider: {fallback.primary}</div>
                <div>fallback: {fallback.fallback}</div>
                {fallback.model ? <div>model: {fallback.model}</div> : null}
              </dl>
            </details>
          </div>
        ) : null}

        {/* ---- stages (§20, §21) ------------------------------------ */}
        {/* Replay controls. The pipeline is already recorded when this renders, so
            "replay" is the honest word - it is not a live feed. */}
        {stages.length > 0 ? (
          <div className="mb-2 flex items-center gap-2">
            {playing ? (
              <>
                <span className="flex items-center gap-1.5 text-2xs text-muted">
                  <Spinner size={11} />
                  Replaying the recorded run…
                </span>
                <button
                  type="button"
                  onClick={() => {
                    setPlaying(false);
                    setRevealed(stages.length);
                  }}
                  className="ml-auto inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-2xs text-muted transition hover:border-line-strong hover:text-ink"
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
                Replay the pipeline
              </button>
            )}
          </div>
        ) : null}

        <ol className="space-y-1.5">
          {stages.map((stage, index) => {
            const explanation = stageExplanation(stage.stage);
            const isOpen = open === stage.stage;
            const isHighlighted = highlighted === stage.stage;
            const skipped = stage.status === "skipped";
            const failed = stage.status === "error";
            const details = entries(stage.data);
            const state = stageState(index);

            return (
              <li
                key={`${stage.stage}-${stage.seq}`}
                className={cn(
                  "rounded-xl border transition-all duration-300",
                  isHighlighted
                    ? "border-brand/50 bg-brand/6 ring-2 ring-brand/20"
                    : "border-line bg-surface",
                  // Not yet reached: present but quiet, so the reader can see how
                  // much is left without reading ahead.
                  state === "pending" && "opacity-40",
                  state === "active" && "border-brand/40 shadow-card",
                )}
              >
                <button
                  type="button"
                  onClick={() => setOpen(isOpen ? null : stage.stage)}
                  aria-expanded={isOpen}
                  className="flex w-full items-start gap-2.5 px-3 py-2.5 text-left"
                >
                  <span className="mt-0.5 shrink-0">
                    {state === "active" ? (
                      // The stage being replayed right now.
                      <span className="flex h-3.5 w-3.5 items-center justify-center">
                        <span className="h-2 w-2 animate-pulse rounded-full bg-brand" />
                      </span>
                    ) : failed ? (
                      <AlertTriangle className="h-3.5 w-3.5 text-caution" />
                    ) : skipped ? (
                      <MinusCircle className="h-3.5 w-3.5 text-faint" />
                    ) : state === "pending" ? (
                      <span className="flex h-3.5 w-3.5 items-center justify-center">
                        <span className="h-1.5 w-1.5 rounded-full bg-line-strong" />
                      </span>
                    ) : (
                      <Check className="h-3.5 w-3.5 text-positive" />
                    )}
                  </span>

                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline justify-between gap-2">
                      <span
                        className={cn(
                          "text-2xs font-medium",
                          skipped ? "text-faint" : "text-ink",
                        )}
                      >
                        {stage.label || explanation?.label || stage.stage}
                      </span>
                      <span className="shrink-0 font-mono text-2xs text-faint">
                        {skipped ? "—" : formatDuration(stage.duration_ms)}
                      </span>
                    </span>

                    {/* Level 1: one beginner sentence. */}
                    {explanation ? (
                      <span className="mt-0.5 block text-2xs leading-relaxed text-muted">
                        {explanation.what}
                      </span>
                    ) : null}
                  </span>

                  {isOpen ? (
                    <ChevronDown className="mt-0.5 h-3 w-3 shrink-0 text-faint" />
                  ) : (
                    <ChevronRight className="mt-0.5 h-3 w-3 shrink-0 text-faint" />
                  )}
                </button>

                {/* Level 2: why it matters, the diagram, the real values. */}
                {isOpen ? (
                  <div className="animate-fade-up space-y-3 border-t border-line px-3 py-3">
                    {skipped ? (
                      <p className="rounded-lg border border-caution/25 bg-caution/8 px-2.5 py-2 text-2xs leading-relaxed text-caution">
                        <strong>Not enabled in this configuration.</strong> This stage did not
                        run
                        {stage.data?.reason ? ` — ${String(stage.data.reason)}` : "."} Nothing is
                        shown in its place.
                      </p>
                    ) : null}

                    {explanation ? (
                      <dl className="space-y-2 text-2xs leading-relaxed">
                        <div>
                          <dt className="font-medium text-ink">Why it exists</dt>
                          <dd className="text-muted">{explanation.why}</dd>
                        </div>
                        <div>
                          <dt className="font-medium text-ink">If it were removed</dt>
                          <dd className="text-muted">{explanation.without}</dd>
                        </div>
                      </dl>
                    ) : null}

                    <StageAnimation stage={stage.stage} />

                    {details.length > 0 ? (
                      <details open>
                        <summary className="cursor-pointer text-2xs font-medium text-ink">
                          Measured in this run
                        </summary>
                        <dl className="mt-1.5 space-y-0.5">
                          {details.map(([key, value]) => (
                            <div key={key} className="flex gap-2 text-2xs">
                              <dt className="shrink-0 text-faint">{key.replace(/_/g, " ")}</dt>
                              <dd className="min-w-0 flex-1 break-words font-mono text-ink">
                                {value}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      </details>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ol>

        {/* ---- live trace (§24) ------------------------------------- */}
        {stages.length > 0 ? (
          <div className="mt-4 rounded-xl border border-line">
            <button
              type="button"
              onClick={() => setShowTrace((value) => !value)}
              aria-expanded={showTrace}
              className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
            >
              <Terminal className="h-3.5 w-3.5 text-muted" />
              <span className="flex-1 text-2xs font-medium text-ink">Live RAG trace</span>
              {showTrace ? (
                <ChevronDown className="h-3 w-3 text-faint" />
              ) : (
                <ChevronRight className="h-3 w-3 text-faint" />
              )}
            </button>

            {showTrace ? (
              <div className="animate-fade-up border-t border-line px-3 py-2.5">
                <p className="mb-2 text-2xs leading-relaxed text-faint">
                  The same run as a log. Click an event to highlight its stage.
                </p>
                <ul className="space-y-0.5 font-mono text-2xs">
                  {stages.map((stage) => {
                    const time = formatTime(stage.created_at);
                    return (
                      <li key={`trace-${stage.seq}-${stage.stage}`}>
                        <button
                          type="button"
                          onClick={() =>
                            setHighlighted(
                              highlighted === stage.stage ? null : stage.stage,
                            )
                          }
                          className={cn(
                            "flex w-full items-baseline gap-2 rounded px-1 py-0.5 text-left transition hover:bg-sunken",
                            highlighted === stage.stage && "bg-brand/10",
                          )}
                        >
                          {hasTimes ? (
                            <span className="w-16 shrink-0 text-faint">{time ?? "—"}</span>
                          ) : null}
                          <span className="min-w-0 flex-1 truncate text-ink">
                            {stage.label || stage.stage}
                          </span>
                          <span className="shrink-0 text-faint">
                            {stage.status === "skipped"
                              ? "—"
                              : formatDuration(stage.duration_ms)}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}

        {stages.length === 0 && !running ? (
          <p className="flex items-center gap-1.5 text-2xs text-faint">
            <Eye className="h-3 w-3" />
            Ask a question and the pipeline will appear here.
          </p>
        ) : null}
      </div>
    </div>
  );
}

export default LearningPanel;
