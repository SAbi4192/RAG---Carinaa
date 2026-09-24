import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";

import { cn } from "@/lib/cn";
import { Spinner } from "@/components/ui/Feedback";

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(query.matches);
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return reduced;
}

/**
 * The waiting indicator, animating through the pipeline's real stage names.
 *
 * WHAT THIS IS, AND IS NOT
 * ------------------------
 * The stage names are the pipeline's REAL stages - the same ones the RAG Trace reports.
 * The PACING is presentation only. The backend answers in one request, so this is not a
 * live progress feed, and it deliberately shows NAMES with no durations. Attaching fake
 * millisecond counts to a cycling animation would misrepresent latency, which is the
 * exact thing the project refuses to do.
 *
 * Pacing is short (500 ms per stage) so it reads as alive rather than as a wait. Once the
 * list is exhausted it HOLDS on the final stage instead of looping - a spinner that cycles
 * forever suggests progress that stopped being meaningful.
 *
 * With `prefers-reduced-motion` it shows a single static line, because the whole point of
 * the animation is legibility, and motion that some people cannot comfortably read is not
 * doing its job.
 */
const STAGES = [
  "Query Analysis",
  "Query Embedding",
  "Vector Search",
  "Candidate Retrieval",
  "Re-ranking",
  "Context Building",
  "LLM Generation",
  "Citation Resolution",
  "Grounding",
];

const STAGE_MS = 500;

export function ThinkingStages({
  className,
  offline = false,
  animate = true,
  liveStage = null,
}: {
  className?: string;
  /** Offline runs the local model, which is genuinely slow; say so plainly. */
  offline?: boolean;
  /**
   * Whether to cycle the stage names.
   *
   * OFF on the normal Chat screen by request: it is a plain chatbot, and the cycling
   * names are educational ornament. They belong in Learning Mode only.
   */
  animate?: boolean;
  /**
   * The name of the pipeline stage the SERVER just reported, when a live stream
   * is running. When present, it replaces the timed guess entirely: the line says
   * what is actually happening now rather than cycling on a timer, so a slow or
   * out-of-order stage never shows the wrong name. It is the single field that
   * makes this waiting line honest while a stream is in progress.
   */
  liveStage?: string | null;
}) {
  const reduced = usePrefersReducedMotion();
  const [index, setIndex] = useState(0);

  // This effect MUST run before any early return below. A hook placed after a
  // conditional return changes the hook order between renders (present on one
  // render, absent the next), which React rejects outright and which silently
  // corrupts state. Advancing the timer while a real stage name is shown is
  // harmless: when `liveStage` is set the timer's output is never rendered.
  useEffect(() => {
    if (reduced || liveStage) return;
    if (index >= STAGES.length - 1) return;
    const timer = window.setTimeout(
      () => setIndex((current) => Math.min(current + 1, STAGES.length - 1)),
      STAGE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [index, reduced, liveStage]);

  // A real stage name from the stream always wins over the timer. This is the
  // difference between "we think it's about here" and "the server just told us".
  if (liveStage) {
    return (
      <div className={cn("flex items-center gap-3 text-xs text-muted", className)}>
        <Spinner size={14} />
        <span
          key={liveStage}
          className="animate-fade-up font-medium text-ink"
          aria-live="polite"
          aria-atomic="true"
        >
          {liveStage}
        </span>
        <span className="flex gap-0.5" aria-hidden>
          <span className="h-1 w-1 animate-pulse rounded-full bg-brand" />
          <span className="h-1 w-1 animate-pulse rounded-full bg-brand" style={{ animationDelay: "150ms" }} />
          <span className="h-1 w-1 animate-pulse rounded-full bg-brand" style={{ animationDelay: "300ms" }} />
        </span>
        {offline ? (
          <span className="text-2xs text-faint">The local model can take 30 seconds or more on a laptop.</span>
        ) : null}
      </div>
    );
  }

  // Static wording for Chat (and for reduced motion, or when animation is off).
  if (!animate || reduced) {
    return (
      <div className={cn("flex items-center gap-3 text-xs text-muted", className)}>
        <Spinner size={14} />
        <span>
          {animate ? "Working through the pipeline..." : "Retrieving and generating..."}
          {offline ? " The local model can take 30 seconds or more on a laptop." : ""}
        </span>
      </div>
    );
  }

  const current = STAGES[Math.min(index, STAGES.length - 1)];
  const done = index >= STAGES.length - 1;

  if (reduced) {
    return (
      <div className={cn("flex items-center gap-3 text-xs text-muted", className)}>
        <Spinner size={14} />
        <span>
          {done ? "Finishing up…" : "Working through the pipeline…"}
          {offline ? " The local model can take 30 seconds or more on a laptop." : ""}
        </span>
      </div>
    );
  }

  return (
    <div className={cn("flex items-center gap-3 text-xs text-muted", className)}>
      <Spinner size={14} />

      <span className="flex items-center gap-2">
        {/* The name swaps in place; nothing reflows around it. */}
        <span
          key={current}
          className="animate-fade-up font-medium text-ink"
          aria-live="polite"
          aria-atomic="true"
        >
          {current}
        </span>

        {done ? (
          <span className="flex items-center gap-1 text-2xs text-faint">
            <Sparkles className="h-3 w-3" />
            finishing
          </span>
        ) : (
          <span className="flex gap-0.5" aria-hidden>
            <span className="h-1 w-1 animate-pulse rounded-full bg-brand" />
            <span
              className="h-1 w-1 animate-pulse rounded-full bg-brand"
              style={{ animationDelay: "150ms" }}
            />
            <span
              className="h-1 w-1 animate-pulse rounded-full bg-brand"
              style={{ animationDelay: "300ms" }}
            />
          </span>
        )}
      </span>

      {offline ? (
        <span className="text-2xs text-faint">The local model can take 30 seconds or more on a laptop.</span>
      ) : null}
    </div>
  );
}

export default ThinkingStages;
