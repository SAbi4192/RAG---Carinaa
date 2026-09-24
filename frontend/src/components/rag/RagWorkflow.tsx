import { useState } from "react";
import {
  Boxes,
  ChevronDown,
  FileSearch,
  ListChecks,
  MessageCircleQuestion,
  MessageSquareText,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  Target,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { formatDuration } from "@/lib/format";
import type { TraceStage } from "@/lib/types";
import { stageExplanation } from "./stageExplanations";

/**
 * The RAG architecture workflow, as a product-quality visual.
 *
 * WHY THIS EXISTS IN THIS FORM
 * ----------------------------
 * A flowchart built for engineers ("embed → ANN search → cross-encoder") teaches
 * a beginner nothing on first contact. This component draws the same pipeline in
 * the product's own language - Understand, Search, Retrieve, Rank, Prepare, AI,
 * Verify - and lets a curious reader open any node to see the technical stages
 * behind it. The layers are:
 *
 *   1. collapsed node     what this step is, in one line
 *   2. expanded node      what it does, why it exists, and which real stages
 *                         implement it (with their names from the trace)
 *   3. with a run attached: what ACTUALLY happened in that run - duration,
 *                         status, skipped-with-reason - read from the trace
 *
 * That last part is the honesty rule this project runs on: nothing here is
 * drawn from a diagram when a real measurement exists, and nothing is drawn
 * at all when the run never reached a step.
 */

/** Optional real run, so the workflow can report what actually happened. */
export interface WorkflowRun {
  stages: TraceStage[];
}

interface WorkflowNode {
  id: string;
  title: string;
  icon: LucideIcon;
  /** The one-line beginner description on the collapsed node. */
  simple: string;
  /** Technical stage names this node is made of (trace `stage` values). */
  stageKeys: string[];
}

/** In pipeline order. Question and answer are anchors, not stages. */
const WORKFLOW_NODES: WorkflowNode[] = [
  {
    id: "question",
    title: "Your question",
    icon: MessageCircleQuestion,
    simple: "You type a question the way you would ask any chatbot.",
    stageKeys: [],
  },
  {
    id: "understand",
    title: "Understand",
    icon: ScanSearch,
    simple: "Carinaa reads the question and works out what you are asking.",
    stageKeys: ["query_analysis"],
  },
  {
    id: "search",
    title: "Search knowledge",
    icon: FileSearch,
    simple:
      "The question is compared with your documents by meaning, not by exact words.",
    stageKeys: ["query_embedding", "vector_search"],
  },
  {
    id: "retrieve",
    title: "Retrieve",
    icon: Target,
    simple: "The passages that look most relevant are brought back as candidates.",
    stageKeys: ["candidate_retrieval"],
  },
  {
    id: "rank",
    title: "Rank",
    icon: ListChecks,
    simple:
      "Candidates are ordered and near-duplicates dropped, keeping the strongest evidence.",
    stageKeys: ["reranking"],
  },
  {
    id: "context",
    title: "Build context",
    icon: Boxes,
    simple: "The best passages are packed into numbered evidence for the AI to read.",
    stageKeys: ["context_building"],
  },
  {
    id: "ai",
    title: "AI",
    icon: Sparkles,
    simple: "The model writes an answer from your question plus that evidence.",
    stageKeys: ["llm_generation", "web_search", "failsafe"],
  },
  {
    id: "verify",
    title: "Verify",
    icon: ShieldCheck,
    simple:
      "Citations are resolved and the answer is checked against the evidence.",
    stageKeys: ["citation_resolution", "grounding"],
  },
  {
    id: "answer",
    title: "Answer",
    icon: MessageSquareText,
    simple: "You get the answer, its sources, and an honest check of how well it is supported.",
    stageKeys: [],
  },
];

export function RagWorkflow({
  run,
  className,
  defaultOpen,
}: {
  /** A real recorded run - when present, nodes report what actually happened. */
  run?: WorkflowRun;
  className?: string;
  /** Node id to open on first render (e.g. "search"). */
  defaultOpen?: string | null;
}) {
  const [openId, setOpenId] = useState<string | null>(defaultOpen ?? null);

  /** Every trace event belonging to one node, in trace order. */
  const eventsFor = (node: WorkflowNode): TraceStage[] =>
    (run?.stages ?? []).filter((stage) => node.stageKeys.includes(stage.stage));

  /** Summary status of a node given a real run, or null when no run exists. */
  const statusFor = (node: WorkflowNode): "done" | "failed" | "skipped" | null => {
    const events = eventsFor(node);
    if (events.length === 0) return null;
    const hasOk = events.some((event) => event.status === "ok");
    const hasError = events.some((event) => event.status === "error");
    const allSkipped = events.every((event) => event.status === "skipped");
    if (hasOk) return "done";
    if (hasError) return "failed";
    if (allSkipped) return "skipped";
    return "done";
  };

  const runTotal = run?.stages.reduce((sum, stage) => sum + (stage.duration_ms || 0), 0);

  return (
    <div className={cn("relative", className)}>
      {/* One quiet caption for the whole diagram. */}
      <p className="mb-3 text-2xs leading-relaxed text-faint">
        Click any step to see what it does
        {run ? " and what it did in this run" : ""}. Every value shown for a run
        was measured while the pipeline ran.
      </p>

      <ol className="relative space-y-1 pl-1">
        {WORKFLOW_NODES.map((node, index) => {
          const Icon = node.icon;
          const open = openId === node.id;
          const status = statusFor(node);
          const events = eventsFor(node);
          const isAnchor = node.stageKeys.length === 0;

          return (
            <li key={node.id} className="relative">
              {/* The rail: one continuous line through every dot. */}
              {index < WORKFLOW_NODES.length - 1 ? (
                <span
                  aria-hidden
                  className="absolute left-[17px] top-9 h-[calc(100%-1.5rem)] w-px bg-line"
                />
              ) : null}

              <button
                type="button"
                onClick={() => setOpenId(open ? null : node.id)}
                aria-expanded={open}
                className={cn(
                  "group relative flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left transition-colors",
                  open ? "bg-brand/[0.06]" : "hover:bg-sunken/70",
                )}
              >
                <span
                  className={cn(
                    "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border transition-colors",
                    status === "done"
                      ? "border-positive/40 bg-positive/10 text-positive"
                      : status === "failed"
                        ? "border-negative/40 bg-negative/10 text-negative"
                        : status === "skipped"
                          ? "border-line bg-sunken text-faint"
                          : isAnchor
                            ? "border-brand/30 bg-brand/10 text-brand"
                            : "border-line-strong bg-surface text-muted",
                  )}
                >
                  <Icon className="h-4 w-4" />
                </span>

                <span className="min-w-0 flex-1">
                  <span className="block text-xs font-semibold text-ink">{node.title}</span>
                  <span className="mt-0.5 block truncate text-2xs text-muted">
                    {node.simple}
                  </span>
                </span>

                {run && status ? (
                  <span
                    className={cn(
                      "shrink-0 rounded-full px-1.5 py-0.5 text-[0.625rem] font-medium",
                      status === "done"
                        ? "bg-positive/10 text-positive"
                        : status === "failed"
                          ? "bg-negative/10 text-negative"
                          : "bg-sunken text-faint",
                    )}
                    title={
                      status === "done"
                        ? "Completed in this run"
                        : status === "failed"
                          ? "Failed in this run"
                          : "Not used in this run"
                    }
                  >
                    {status === "done" ? "✓" : status === "failed" ? "✗" : "—"}
                  </span>
                ) : null}

                <ChevronDown
                  className={cn(
                    "h-3.5 w-3.5 shrink-0 text-faint transition-transform",
                    open && "rotate-180",
                  )}
                />
              </button>


              {/* ---- the three questions: what / why / what happened ------- */}
              {open ? (
                <div className="animate-slide-down mb-1 ml-12 rounded-xl border border-line bg-sunken/60 p-3">
                  <dl className="space-y-2 text-2xs leading-relaxed">
                    <div>
                      <dt className="font-medium text-ink">What is this?</dt>
                      <dd className="text-muted">{node.simple}</dd>
                    </div>
                    {(() => {
                      const key = node.stageKeys[0];
                      const explanation = key ? stageExplanation(key) : undefined;
                      if (!explanation) return null;
                      return (
                        <div>
                          <dt className="font-medium text-ink">Why it exists</dt>
                          <dd className="text-muted">{explanation.why}</dd>
                        </div>
                      );
                    })()}
                  </dl>

                  {/* Technical layer - the names behind this friendly step. */}
                  {node.stageKeys.length > 0 ? (
                    <div className="mt-2.5 border-t border-line pt-2">
                      <p className="text-[0.625rem] font-semibold uppercase tracking-wider text-faint">
                        Technical stages
                      </p>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {node.stageKeys.map((key) => {
                          const explanation = stageExplanation(key);
                          return (
                            <span
                              key={key}
                              title={explanation?.label ?? key}
                              className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[0.625rem] text-muted"
                            >
                              {explanation?.label ?? key}
                            </span>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}

                  {/* The run - real measurements only, or an honest absence. */}
                  {run && node.stageKeys.length > 0 ? (
                    <div className="mt-2.5 border-t border-line pt-2">
                      <p className="text-[0.625rem] font-semibold uppercase tracking-wider text-faint">
                        What happened in this run
                      </p>
                      {events.length === 0 ? (
                        <p className="mt-1 text-2xs leading-relaxed text-faint">
                          This step did not appear in the recorded run.
                        </p>
                      ) : (
                        <ul className="mt-1 space-y-1">
                          {events.map((event) => (
                            <li
                              key={`${event.seq}-${event.stage}`}
                              className="flex items-baseline justify-between gap-2 text-2xs"
                            >
                              <span className="min-w-0 flex-1 truncate text-muted">
                                {event.label || event.stage}
                                {event.status === "skipped" && event.data?.reason ? (
                                  <span className="text-faint">
                                    {" — "}
                                    {String(event.data.reason)}
                                  </span>
                                ) : null}
                              </span>
                              <span className="shrink-0 font-mono text-faint">
                                {event.status === "skipped"
                                  ? "not used"
                                  : formatDuration(event.duration_ms)}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>

      {run && runTotal ? (
        <p className="mt-3 border-t border-line pt-2.5 font-mono text-[0.625rem] text-faint">
          total measured pipeline time · {formatDuration(runTotal)}
        </p>
      ) : null}
    </div>
  );
}

export default RagWorkflow;

