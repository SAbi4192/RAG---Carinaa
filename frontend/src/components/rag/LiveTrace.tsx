import { Fragment, useMemo } from "react";
import { AlertTriangle, CheckCircle2, MinusCircle, Terminal } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/cn";
import { formatDuration } from "@/lib/format";
import type { TraceStage } from "@/lib/types";

/**
 * The Live RAG Trace: the same run, read as a log.
 *
 * The pipeline view answers "what are the stages and what does each mean". This
 * answers a different question - "what happened, in what order, and when". Both are
 * real views of one recorded trace, and they are built from the same data.
 *
 * `created_at` is a genuine stored timestamp, so the times below are when each
 * stage actually finished. Older traces predate that field; when it is missing the
 * time column is omitted rather than back-filled from durations, because a time
 * calculated backwards would look identical on screen and be a guess.
 */

const STATUS_ICON: Record<string, LucideIcon> = {
  ok: CheckCircle2,
  skipped: MinusCircle,
  error: AlertTriangle,
};

const STATUS_TONE: Record<string, string> = {
  ok: "text-positive",
  skipped: "text-faint",
  error: "text-caution",
};

/** Keys worth echoing, in the order they should appear. */
const DETAIL_KEYS = [
  "candidates_retrieved",
  "candidates_returned",
  "chunks_found",
  "chunks_selected",
  "excerpts",
  "citations_found",
  "cited_numbers",
  "invalid_numbers",
  "dropped",
  "characters",
  "budget",
  "candidates",
  "kept",
  "moved",
  "error",
  "action",
  "reason",
];

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

function detailLines(stage: TraceStage): string[] {
  const lines: string[] = [];
  for (const key of DETAIL_KEYS) {
    const value = stage.data?.[key];
    if (value === undefined || value === null || value === "") continue;

    const rendered =
      typeof value === "object"
        ? Array.isArray(value)
          ? value.length
            ? value.join(", ")
            : "none"
          : `${Object.keys(value).length} fields`
        : String(value);

    lines.push(`${key.replace(/_/g, " ")}: ${rendered}`);
  }
  return lines.slice(0, 4);
}

export function LiveTrace({
  stages,
  className,
  emptyLabel = "No events were recorded for this answer.",
}: {
  stages: TraceStage[];
  className?: string;
  emptyLabel?: string;
}) {
  const hasTimes = useMemo(
    () => stages.some((stage) => Boolean(stage.created_at)),
    [stages],
  );

  if (stages.length === 0) {
    return (
      <p className={cn("text-2xs text-faint", className)}>{emptyLabel}</p>
    );
  }

  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full border-collapse font-mono text-2xs">
        <tbody>
          {stages.map((stage) => {
            const Icon = STATUS_ICON[stage.status] ?? MinusCircle;
            const time = formatTime(stage.created_at);
            const details = detailLines(stage);

            return (
              <Fragment key={`${stage.seq}-${stage.stage}`}>
                <tr className="align-top">
                  <td className="w-16 whitespace-nowrap py-1 pr-3 text-faint">
                    {hasTimes ? (time ?? "—") : null}
                  </td>
                  <td className="w-4 py-1.5 pr-1.5">
                    <Icon className={cn("h-3 w-3", STATUS_TONE[stage.status] ?? "text-faint")} />
                  </td>
                  <td className="py-1 pr-3 text-ink">{stage.label || stage.stage}</td>
                  <td className="whitespace-nowrap py-1 text-right text-faint">
                    {stage.status === "skipped" ? "—" : formatDuration(stage.duration_ms)}
                  </td>
                </tr>

                {details.length > 0 ? (
                  <tr>
                    <td />
                    <td />
                    <td colSpan={2} className="pb-1.5 pr-3 text-faint">
                      {details.map((line) => (
                        <span key={line} className="mr-3 inline-block">
                          {line}
                        </span>
                      ))}
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>

      {!hasTimes ? (
        <p className="mt-2 text-2xs leading-relaxed text-faint">
          This trace was recorded before wall-clock times were stored, so no time column is
          shown. Durations are still real.
        </p>
      ) : null}
    </div>
  );
}

/** A small labelled wrapper, used when the trace sits inside another panel. */
export function LiveTracePanel({
  stages,
  title = "Live RAG trace",
  description = "Every line below was written by the server while answering this question.",
  className,
}: {
  stages: TraceStage[];
  title?: string;
  description?: string;
  className?: string;
}) {
  return (
    <div className={cn("rounded-xl border border-line bg-sunken", className)}>
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <Terminal className="h-3.5 w-3.5 text-muted" />
        <p className="text-2xs font-medium text-ink">{title}</p>
      </div>
      <div className="px-4 py-3">
        <p className="mb-2.5 text-2xs leading-relaxed text-faint">{description}</p>
        <LiveTrace stages={stages} />
      </div>
    </div>
  );
}

export default LiveTrace;
