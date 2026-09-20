import { useState } from "react";
import { ChevronDown, ListTree, Loader2 } from "lucide-react";

import { cn } from "@/lib/cn";
import { api } from "@/lib/api";
import type { Trace } from "@/lib/types";
import { useAsync } from "@/hooks/useAsync";
import { LiveTracePanel } from "./LiveTrace";
import { RagPipeline } from "./RagPipeline";

/**
 * "Watch RAG think" - the pipeline for one answer, inline in the chat.
 *
 * THE DATA IS REAL, AND THAT SHAPES THE DESIGN
 * --------------------------------------------
 * The trace is fetched from the backend per message, so this works both for a
 * freshly-asked question and for a conversation reloaded tomorrow. Nothing is
 * replayed from memory and nothing is simulated.
 *
 * That has one visible consequence worth being deliberate about: the pipeline is
 * shown AFTER the answer arrives, not before it. Animating it beforehand would
 * mean animating a guess about what the system was about to do, and the whole
 * point of this feature is that a learner can trust what they are watching. The
 * stagger on reveal preserves the sense of sequence without inventing it.
 *
 * The fetch is lazy: nothing is requested until the user expands it, so learning
 * mode costs nothing for someone who never opens a pipeline.
 */
export function MessagePipeline({
  messageId,
  defaultOpen = false,
  /** Collapsed by default in long conversations; the newest answer opens itself. */
  className,
}: {
  messageId: number;
  defaultOpen?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);

  const trace = useAsync<Trace>(open ? () => api.chat.trace(messageId) : null, [messageId, open]);

  return (
    <div className={cn("mt-2.5", className)}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-2xs font-medium transition",
          open
            ? "border-brand/40 bg-brand/8 text-brand"
            : "border-line bg-surface text-muted hover:border-line-strong hover:text-ink",
        )}
      >
        {trace.loading ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <ListTree className="h-3 w-3" />
        )}
        {open ? "Hide the pipeline" : "Show how this answer was built"}
        <ChevronDown className={cn("h-3 w-3 transition-transform", open && "rotate-180")} />
      </button>

      {open ? (
        <div className="mt-2 animate-fade-up space-y-3 rounded-xl border border-line bg-surface p-4">
          {trace.error ? (
            <p className="text-2xs text-negative">{trace.error}</p>
          ) : trace.loading ? (
            <p className="text-2xs text-faint">Loading the pipeline…</p>
          ) : trace.data ? (
            <>
              <RagPipeline stages={trace.data.stages} totalMs={trace.data.total_ms} animate />
              <LiveTracePanel
                stages={trace.data.stages}
                description="The same run, read as a log. Times are when each stage finished."
              />
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default MessagePipeline;
