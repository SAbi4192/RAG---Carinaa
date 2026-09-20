import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Info,
  MinusCircle,
  Play,
  RotateCcw,
  Timer,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { formatDuration } from "@/lib/format";
import type { TraceStage } from "@/lib/types";
import { stageExplanation } from "./stageExplanations";

/**
 * The pipeline, drawn from real trace data.
 *
 * THE ONE RULE THIS COMPONENT FOLLOWS
 * -----------------------------------
 * It renders what the backend actually did, and nothing else. It never advances a
 * timer to look busy, and a stage that did not run says so.
 *
 * That rule is the whole reason the project is worth building. A teaching tool
 * that animates a plausible-looking pipeline is worse than no teaching tool,
 * because the learner cannot tell the animation from the system. So:
 *
 *   - every stage's status, duration and detail come from `TraceStage`
 *   - `skipped` renders as "not enabled in this configuration" WITH the reason
 *   - the sequence is driven by data that already exists, so the animation is a
 *     *replay* of a real run rather than a simulation of one
 *
 * The reveal animation is therefore a presentation of measured facts, not a
 * fabrication of activity. When the answer arrives, the whole trace is already
 * known; we stagger its display so a human can follow the order.
 */

interface RagPipelineProps {
  stages: TraceStage[];
  /** Total measured time for the whole run. */
  totalMs?: number;
  /** Replay the reveal animation on mount. */
  animate?: boolean;
  /** Which stage to highlight (e.g. the one the user clicked a citation in). */
  highlightStage?: string;
  /** Compact mode drops the connectors and the inline detail. */
  compact?: boolean;
  className?: string;
}

const STATUS_META: Record<
  string,
  { tone: string; ring: string; label: string; icon: typeof Check }
> = {
  ok: {
    tone: "border-positive/30 bg-positive/10 text-positive",
    ring: "border-positive/40 bg-positive/12 text-positive",
    label: "ran",
    icon: Check,
  },
  skipped: {
    tone: "border-line bg-sunken text-faint",
    ring: "border-line bg-sunken text-faint",
    label: "skipped",
    icon: MinusCircle,
  },
  error: {
    tone: "border-caution/35 bg-caution/10 text-caution",
    ring: "border-caution/45 bg-caution/12 text-caution",
    label: "failed",
    icon: AlertTriangle,
  },
};

function statusMeta(status: string) {
  return STATUS_META[status] ?? STATUS_META.skipped;
}

/** Render a stage's real detail values as short readable chips. */
function detailEntries(data: Record<string, unknown>, limit = 6): [string, string][] {
  const skip = new Set(["label", "stage", "status", "seq", "trace_id"]);
  const out: [string, string][] = [];

  for (const [key, value] of Object.entries(data ?? {})) {
    if (skip.has(key)) continue;
    if (value === null || value === undefined || value === "") continue;
    if (typeof value === "object") {
      // Objects are summarised by their size rather than dumped. The RAG Trace
      // page shows the raw payload for anyone who wants it.
      const size = Array.isArray(value) ? value.length : Object.keys(value).length;
      out.push([key, `${size} item${size === 1 ? "" : "s"}`]);
    } else {
      out.push([key, String(value)]);
    }
    if (out.length >= limit) break;
  }
  return out;
}

function humanise(key: string): string {
  return key.replace(/_/g, " ");
}

export function RagPipeline({
  stages,
  totalMs,
  animate = true,
  highlightStage,
  compact = false,
  className,
}: RagPipelineProps) {
  const [revealed, setRevealed] = useState(animate ? 0 : stages.length);
  const [open, setOpen] = useState<string | null>(null);

  // Stagger the reveal so the order of the pipeline is legible. Each step is
  // driven by a real stage that already happened - nothing here invents work.
  useEffect(() => {
    if (!animate) {
      setRevealed(stages.length);
      return;
    }
    setRevealed(0);
    if (stages.length === 0) return;

    let index = 0;
    const step = () => {
      index += 1;
      setRevealed(index);
      if (index < stages.length) {
        timer = window.setTimeout(step, 260);
      }
    };
    let timer = window.setTimeout(step, 120);
    return () => window.clearTimeout(timer);
  }, [stages, animate]);

  const measured = useMemo(
    () => stages.filter((s) => s.status === "ok").reduce((sum, s) => sum + (s.duration_ms || 0), 0),
    [stages],
  );

  if (stages.length === 0) {
    return (
      <div className={cn("rounded-xl border border-line bg-sunken px-4 py-6 text-center", className)}>
        <p className="text-xs text-muted">No pipeline trace is available for this answer.</p>
      </div>
    );
  }

  return (
    <div className={cn("relative", className)}>
      <div className="mb-3 flex flex-wrap items-center gap-3 text-2xs text-faint">
        <span className="inline-flex items-center gap-1.5">
          <Play className="h-3 w-3" />
          {stages.length} stages, in the order they ran
        </span>
        {totalMs ? (
          <span className="inline-flex items-center gap-1.5">
            <Timer className="h-3 w-3" />
            {formatDuration(totalMs)} total
            {measured ? ` · ${formatDuration(measured)} measured in stages` : ""}
          </span>
        ) : null}
        <span className="inline-flex items-center gap-1.5 text-faint/80">
          <Info className="h-3 w-3" />
          every value below was measured during this answer
        </span>
      </div>

      <ol className="relative space-y-0">
        {stages.map((stage, index) => {
          const explanation = stageExplanation(stage.stage);
          const meta = statusMeta(stage.status);
          const Icon = explanation?.icon ?? Info;
          const StatusIcon = meta.icon;
          const isRevealed = index < revealed;
          const isOpen = open === stage.stage;
          const isHighlighted = highlightStage === stage.stage;
          const isLast = index === stages.length - 1;
          const details = detailEntries(stage.data ?? {});

          return (
            <li key={`${stage.stage}-${stage.seq}`} className="relative">
              {/* connector */}
              {!compact && !isLast ? (
                <span
                  aria-hidden
                  className={cn(
                    "absolute left-[15px] top-[34px] w-px",
                    "h-[calc(100%-18px)]",
                    isRevealed ? "bg-line-strong" : "bg-line",
                  )}
                />
              ) : null}

              <div
                className={cn(
                  "relative flex gap-3 transition-all duration-300",
                  isRevealed ? "translate-y-0 opacity-100" : "translate-y-1 opacity-0",
                )}
              >
                {/* status node */}
                <span
                  className={cn(
                    "z-10 mt-1.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border",
                    meta.ring,
                    isHighlighted && "ring-2 ring-brand/40",
                  )}
                >
                  <StatusIcon className="h-3.5 w-3.5" />
                </span>

                <div className="min-w-0 flex-1 pb-3">
                  <button
                    type="button"
                    onClick={() => setOpen(isOpen ? null : stage.stage)}
                    aria-expanded={isOpen}
                    className={cn(
                      "group flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left transition",
                      "hover:border-line-strong hover:bg-raised",
                      isHighlighted ? "border-brand/40 bg-brand/5" : "border-line bg-surface",
                    )}
                  >
                    <Icon className="h-3.5 w-3.5 shrink-0 text-muted" />
                    <span className="min-w-0 flex-1 truncate text-xs font-medium text-ink">
                      {stage.label || explanation?.label || stage.stage}
                    </span>

                    {stage.duration_ms ? (
                      <span className="shrink-0 font-mono text-2xs text-faint">
                        {formatDuration(stage.duration_ms)}
                      </span>
                    ) : null}

                    <span
                      className={cn(
                        "shrink-0 rounded-full border px-2 py-0.5 text-2xs font-medium",
                        meta.tone,
                      )}
                    >
                      {meta.label}
                    </span>

                    <ChevronDown
                      className={cn(
                        "h-3.5 w-3.5 shrink-0 text-faint transition-transform",
                        isOpen && "rotate-180",
                      )}
                    />
                  </button>

                  {/* inline detail chips */}
                  {!compact && isRevealed && details.length > 0 && !isOpen ? (
                    <div className="mt-1.5 flex flex-wrap gap-1.5 pl-1">
                      {details.slice(0, 4).map(([key, value]) => (
                        <span
                          key={key}
                          className="inline-flex items-center gap-1 rounded-md border border-line bg-sunken px-1.5 py-0.5 text-2xs text-muted"
                        >
                          <span className="text-faint">{humanise(key)}</span>
                          <span className="font-mono text-ink">{value}</span>
                        </span>
                      ))}
                    </div>
                  ) : null}

                  {/* expanded explanation + real detail */}
                  {isOpen ? (
                    <div className="mt-2 animate-fade-up space-y-3 rounded-xl border border-line bg-sunken p-3.5">
                      {stage.status === "skipped" ? (
                        <p className="flex items-start gap-2 rounded-lg border border-caution/25 bg-caution/8 px-3 py-2 text-2xs leading-relaxed text-caution">
                          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                          <span>
                            This stage is <strong>not enabled in the current configuration</strong>, so
                            it did not run.
                            {stage.data && "reason" in stage.data ? (
                              <> Reason: {String(stage.data.reason)}</>
                            ) : null}{" "}
                            Nothing was faked in its place.
                          </span>
                        </p>
                      ) : null}

                      {explanation ? (
                        <dl className="space-y-2 text-2xs leading-relaxed">
                          <div>
                            <dt className="font-medium text-ink">What happens</dt>
                            <dd className="text-muted">{explanation.what}</dd>
                          </div>
                          <div>
                            <dt className="font-medium text-ink">Why it exists</dt>
                            <dd className="text-muted">{explanation.why}</dd>
                          </div>
                          <div>
                            <dt className="font-medium text-ink">If it were removed</dt>
                            <dd className="text-muted">{explanation.without}</dd>
                          </div>
                          <div className="flex flex-wrap gap-x-6 gap-y-1 pt-1">
                            <div>
                              <dt className="font-medium text-ink">Implemented in</dt>
                              <dd className="font-mono text-faint">{explanation.where}</dd>
                            </div>
                            <div>
                              <dt className="font-medium text-ink">Look for</dt>
                              <dd className="text-muted">{explanation.lookFor}</dd>
                            </div>
                          </div>
                        </dl>
                      ) : null}

                      {details.length > 0 ? (
                        <div>
                          <p className="mb-1.5 text-2xs font-medium text-ink">
                            Measured in this run
                          </p>
                          <div className="flex flex-wrap gap-1.5">
                            {details.map(([key, value]) => (
                              <span
                                key={key}
                                className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-1.5 py-0.5 text-2xs"
                              >
                                <span className="text-faint">{humanise(key)}</span>
                                <span className="font-mono text-ink">{value}</span>
                              </span>
                            ))}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
            </li>
          );
        })}
      </ol>

      {animate && revealed < stages.length ? (
        <button
          type="button"
          onClick={() => setRevealed(stages.length)}
          className="mt-1 inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-2xs text-muted transition hover:border-line-strong hover:text-ink"
        >
          <RotateCcw className="h-3 w-3" />
          Skip to the end
        </button>
      ) : null}
    </div>
  );
}

export default RagPipeline;
