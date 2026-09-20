import { useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  CircleSlash,
  Cpu,
  FileSearch,
  Layers,
  Quote,
  ScanSearch,
  Shield,
  Sparkles,
  Waypoints,
  XCircle,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { formatDuration, formatNumber, formatScore } from "@/lib/format";
import type { Trace, TraceStage } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";

/**
 * RAG Trace timeline.
 *
 * WHAT THIS COMPONENT IS FOR
 * --------------------------
 * This is the screen that justifies the whole project. Anyone can produce an
 * answer; the interesting question is how. So the trace shows every stage the
 * query actually passed through, in order, with:
 *
 *   * the real duration of each stage,
 *   * the real counts and scores it produced,
 *   * and, critically, which stages were SKIPPED.
 *
 * The skipped case matters more than it might appear. Re-ranking is off by
 * default, and web search is opt-in. Showing them as "skipped - not enabled" is
 * honest; omitting them, or showing them as a zero-duration success, would imply
 * work that never happened. This is the same principle as never faking progress.
 *
 * WHAT IT DELIBERATELY DOES NOT SHOW
 * ----------------------------------
 * No API keys, no hidden prompts, and no private chain-of-thought. The backend
 * strips forbidden keys before storing anything, and this component only renders
 * what the backend returns - it never requests more.
 */

const STAGE_ICONS: Record<string, React.ReactNode> = {
  query_analysis: <FileSearch className="h-3.5 w-3.5" />,
  query_embedding: <Cpu className="h-3.5 w-3.5" />,
  vector_search: <ScanSearch className="h-3.5 w-3.5" />,
  candidate_retrieval: <Layers className="h-3.5 w-3.5" />,
  reranking: <Layers className="h-3.5 w-3.5" />,
  context_building: <Quote className="h-3.5 w-3.5" />,
  llm_generation: <Sparkles className="h-3.5 w-3.5" />,
  citation_resolution: <Waypoints className="h-3.5 w-3.5" />,
  grounding: <Shield className="h-3.5 w-3.5" />,
  web_search: <FileSearch className="h-3.5 w-3.5" />,
  failsafe: <Shield className="h-3.5 w-3.5" />,
};

function statusMeta(status: string) {
  if (status === "skipped") {
    return {
      icon: <CircleSlash className="h-3.5 w-3.5 text-faint" />,
      dot: "bg-line-strong",
      tone: "text-faint",
      label: "skipped",
    };
  }
  if (status === "error") {
    return {
      icon: <XCircle className="h-3.5 w-3.5 text-negative" />,
      dot: "bg-negative",
      tone: "text-negative",
      label: "error",
    };
  }
  return {
    icon: <CheckCircle2 className="h-3.5 w-3.5 text-positive" />,
    dot: "bg-positive",
    tone: "text-muted",
    label: "ok",
  };
}

/** Render a stage payload value compactly. */
function renderValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") {
    return Number.isInteger(value) ? formatNumber(value) : formatScore(value);
  }
  if (Array.isArray(value)) {
    if (!value.length) return "none";
    return value.map((item) => renderValue(item)).join(", ");
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) return "—";
    return entries.map(([key, item]) => `${key}=${renderValue(item)}`).join(" · ");
  }
  return String(value);
}

function StageRow({
  stage,
  maxDuration,
  index,
}: {
  stage: TraceStage;
  maxDuration: number;
  index: number;
}) {
  const [open, setOpen] = useState(false);
  const meta = statusMeta(stage.status);
  const skipped = stage.status === "skipped";

  const dataEntries = Object.entries(stage.data ?? {}).filter(
    ([, value]) => value !== null && value !== undefined && value !== "",
  );

  // Width is proportional to the slowest stage, so the waterfall shows where the
  // time actually went. A skipped stage has no bar at all - it did no work.
  const widthPct = maxDuration > 0 && !skipped ? (stage.duration_ms / maxDuration) * 100 : 0;

  return (
    <li
      className="relative animate-fade-up"
      style={{ animationDelay: `${Math.min(index * 40, 320)}ms` }}
    >
      {/* connector */}
      {index > 0 ? (
        <span className="absolute left-[0.6875rem] top-0 h-3 w-px bg-line" aria-hidden />
      ) : null}

      <div
        className={cn(
          "group relative rounded-lg border transition-colors",
          skipped ? "border-line bg-sunken/40" : "border-line bg-surface hover:border-line-strong",
        )}
      >
        <button
          type="button"
          onClick={() => dataEntries.length && setOpen((value) => !value)}
          aria-expanded={dataEntries.length ? open : undefined}
          className={cn(
            "flex w-full items-start gap-3 px-3 py-2.5 text-left",
            !dataEntries.length && "cursor-default",
          )}
        >
          {/* status dot + connector to the icon column */}
          <span className="relative mt-1 flex shrink-0 items-center">
            <span className={cn("h-2 w-2 rounded-full", meta.dot)} />
          </span>

          <span className={cn("mt-0.5 shrink-0", meta.tone)}>
            {STAGE_ICONS[stage.stage] ?? <Layers className="h-3.5 w-3.5" />}
          </span>

          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2">
              <span
                className={cn(
                  "text-xs font-medium",
                  skipped ? "text-faint" : "text-ink",
                )}
              >
                {stage.label}
              </span>
              <span className="font-mono text-2xs text-faint">{stage.stage}</span>
              {skipped ? (
                <Badge tone="neutral" className="text-faint">
                  did not run
                </Badge>
              ) : null}
            </span>

            {/* waterfall bar */}
            {!skipped && maxDuration > 0 ? (
              <span className="mt-1.5 block h-1 w-full overflow-hidden rounded-full bg-sunken">
                <span
                  className="block h-full rounded-full bg-brand/60 transition-[width] duration-700 ease-smooth"
                  style={{ width: `${Math.max(widthPct, 1.5)}%` }}
                />
              </span>
            ) : null}
          </span>

          <span className="flex shrink-0 items-center gap-2">
            <span
              className={cn(
                "font-mono text-2xs tabular-nums",
                skipped ? "text-faint" : "text-muted",
              )}
            >
              {skipped ? "—" : formatDuration(stage.duration_ms)}
            </span>
            {dataEntries.length ? (
              <ChevronDown
                className={cn("h-3 w-3 text-faint transition-transform", open && "rotate-180")}
              />
            ) : null}
          </span>
        </button>

        {open && dataEntries.length ? (
          <div className="animate-slide-down border-t border-line px-3 py-2.5">
            <dl className="grid gap-x-5 gap-y-2 sm:grid-cols-2">
              {dataEntries.map(([key, value]) => (
                <div key={key} className="min-w-0">
                  <dt className="text-2xs uppercase tracking-wide text-faint">
                    {key.replace(/_/g, " ")}
                  </dt>
                  <dd className="mt-0.5 break-words font-mono text-2xs text-ink">
                    {renderValue(value)}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ) : null}
      </div>
    </li>
  );
}

export function TraceTimeline({ trace, className }: { trace: Trace; className?: string }) {
  const stages = trace.stages ?? [];

  const maxDuration = useMemo(
    () => Math.max(...stages.map((stage) => stage.duration_ms), 1),
    [stages],
  );

  // Time actually attributed to stages vs the reported total. A gap means work
  // happened outside the instrumented stages (serialisation, I/O), which is worth
  // surfacing rather than hiding.
  const measured = useMemo(
    () => stages.reduce((sum, stage) => sum + stage.duration_ms, 0),
    [stages],
  );

  const skippedCount = stages.filter((stage) => stage.status === "skipped").length;

  return (
    <Card className={className}>
      <CardHeader
        title="Pipeline trace"
        description={trace.note}
        icon={<Waypoints className="h-4 w-4" />}
        actions={
          <div className="flex items-center gap-1.5">
            <Badge tone="neutral" mono>
              {stages.length} stages
            </Badge>
            <Badge tone="accent" mono>
              {formatDuration(trace.total_ms)}
            </Badge>
          </div>
        }
      />

      {/* summary strip */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-b border-line px-5 py-3 sm:grid-cols-4">
        <SummaryItem label="Total" value={formatDuration(trace.total_ms)} />
        <SummaryItem label="Measured in stages" value={formatDuration(measured)} />
        <SummaryItem
          label="Skipped"
          value={`${skippedCount} of ${stages.length}`}
          hint="Stages that did not run"
        />
        <SummaryItem
          label="Slowest"
          value={
            stages.length
              ? `${formatDuration(maxDuration)}`
              : "—"
          }
          hint={
            stages.length
              ? stages.reduce((slowest, stage) =>
                  stage.duration_ms > slowest.duration_ms ? stage : slowest,
                ).label
              : undefined
          }
        />
      </div>

      <ol className="space-y-1.5 px-5 py-4">
        {stages.map((stage, index) => (
          <StageRow
            key={`${stage.seq}-${stage.stage}`}
            stage={stage}
            maxDuration={maxDuration}
            index={index}
          />
        ))}
      </ol>

      <div className="border-t border-line px-5 py-3">
        <p className="text-2xs leading-relaxed text-faint">
          Every value above was recorded while the query ran. Durations are wall-clock
          measurements, not estimates. Stages that did not execute are marked as skipped
          rather than shown with a duration of zero, because "did not run" and "ran
          instantly" are different facts.
        </p>
      </div>
    </Card>
  );
}

function SummaryItem({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="min-w-0">
      <p className="text-2xs uppercase tracking-wide text-faint">{label}</p>
      <p className="mt-0.5 font-mono text-xs tabular-nums text-ink">{value}</p>
      {hint ? <p className="truncate text-2xs text-faint">{hint}</p> : null}
    </div>
  );
}
