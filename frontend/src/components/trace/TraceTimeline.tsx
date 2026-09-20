import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
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

/**
 * Merge a failed primary generation with the fallback that rescued it.
 *
 * A provider failure followed by a fallback is ONE event in the reader's mind - "the
 * answer was generated, after a retry" - but it arrives as two `llm_generation` rows,
 * which reads as two unrelated stages that both happen to be called the same thing.
 * Worse, the second row looks like an unexplained duplicate.
 *
 * Grouping is deliberately narrow: it only fires when an ERROR generation is followed
 * immediately by a SUCCESSFUL one with a different provider. Two successful generations
 * from the same provider would be a real duplicate and should stay visible as such.
 */
export interface GenerationGroup {
  kind: "generation-group";
  primary: TraceStage;
  fallback: TraceStage;
}

export type TraceEntry = TraceStage | GenerationGroup;

export function groupStages(stages: TraceStage[]): TraceEntry[] {
  const entries: TraceEntry[] = [];
  let index = 0;

  while (index < stages.length) {
    const current = stages[index];
    const next = stages[index + 1];

    const isFailedPrimary =
      current.stage === "llm_generation" && current.status === "error";
    const isFallbackRecovery =
      next?.stage === "llm_generation" &&
      next.status === "ok" &&
      String(next.data?.role ?? "") === "fallback";

    if (isFailedPrimary && isFallbackRecovery) {
      entries.push({ kind: "generation-group", primary: current, fallback: next });
      index += 2;
      continue;
    }

    entries.push(current);
    index += 1;
  }

  return entries;
}

/**
 * One row for a failed generation and the fallback that recovered from it.
 *
 * Drawn as a chain rather than two rows, because that is what happened: the primary was
 * tried, it failed for a stated reason, and a different provider produced the answer.
 * The failure and the recovery belong together - separated, the second row reads as an
 * unexplained duplicate and the first as an error that apparently ended the run.
 */
function GenerationGroupRow({
  group,
  maxDuration,
  index,
}: {
  group: GenerationGroup;
  maxDuration: number;
  index: number;
}) {
  const { primary, fallback } = group;
  const total = primary.duration_ms + fallback.duration_ms;
  const width = Math.max(2, Math.round((total / maxDuration) * 100));

  const primaryProvider = String(primary.data?.provider ?? "the primary provider");
  const fallbackProvider = String(fallback.data?.provider ?? "a fallback provider");
  const reason = String(primary.data?.error ?? "unavailable");
  const model = fallback.data?.model ? String(fallback.data.model) : null;

  return (
    <li className="rounded-lg border border-line bg-surface">
      <div className="flex items-start gap-3 px-3 py-2.5">
        <span className="w-5 shrink-0 pt-0.5 text-right font-mono text-2xs text-faint">
          {index + 1}
        </span>

        <span className="mt-0.5 shrink-0 text-caution">
          <AlertTriangle className="h-3.5 w-3.5" />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <span className="text-xs font-medium text-ink">
              LLM Generation
              <span className="ml-2 rounded border border-caution/40 bg-caution/10 px-1.5 py-px text-[0.625rem] font-normal text-caution">
                recovered
              </span>
            </span>
            <span className="font-mono text-2xs text-faint">{formatDuration(total)}</span>
          </div>

          {/* The chain. */}
          <ol className="mt-1.5 space-y-0.5">
            <li className="flex flex-wrap items-center gap-x-2 text-2xs">
              <span className="text-ink">{primaryProvider}</span>
              <span className="flex items-center gap-1 text-caution">
                <AlertTriangle className="h-3 w-3" />
                {reason}
              </span>
            </li>
            <li className="pl-1 text-2xs text-faint" aria-hidden>
              ↓
            </li>
            <li className="flex flex-wrap items-center gap-x-2 text-2xs">
              <span className="text-ink">{fallbackProvider}</span>
              <span className="flex items-center gap-1 text-positive">
                <Check className="h-3 w-3" />
                generated the response
              </span>
              <span className="font-mono text-faint">{formatDuration(fallback.duration_ms)}</span>
            </li>
          </ol>

          {model ? (
            <p className="mt-1 font-mono text-[0.625rem] text-faint">model: {model}</p>
          ) : null}

          <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-sunken">
            <div className="h-full rounded-full bg-caution/60" style={{ width: `${width}%` }} />
          </div>
        </div>
      </div>
    </li>
  );
}

export function TraceTimeline({ trace, className }: { trace: Trace; className?: string }) {
  const stages = trace.stages ?? [];
  const entries = useMemo(() => groupStages(stages), [stages]);

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
        {entries.map((entry, index) =>
          "kind" in entry ? (
            <GenerationGroupRow
              key={`group-${entry.primary.seq}`}
              group={entry}
              maxDuration={maxDuration}
              index={index}
            />
          ) : (
            <StageRow
              key={`${entry.seq}-${entry.stage}`}
              stage={entry}
              maxDuration={maxDuration}
              index={index}
            />
          ),
        )}
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
