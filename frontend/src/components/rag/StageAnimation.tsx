import { Fragment } from "react";
import { ArrowDown, Database, FileText, Layers, Sparkles } from "lucide-react";

import { cn } from "@/lib/cn";

/**
 * A tiny conceptual diagram per pipeline stage.
 *
 * WHAT THIS IS, AND IS NOT
 * ------------------------
 * These are *conceptual* pictures of what each stage does - the same thing the
 * diagrams in a textbook show, except here the numbers next to them are real. They
 * are labelled as conceptual in the UI, because showing a stylised diagram next to a
 * real measurement invites the reader to assume the diagram is measured too.
 *
 * CONSTRAINTS
 * -----------
 * Cheap by design. CSS transitions only, no requestAnimationFrame loops, nothing
 * that runs forever. A stage animation starts when its stage is shown and stops when
 * it finishes. The brief is explicit that the answer must never be delayed by the
 * educational layer, and an animation that kept the main thread busy would do exactly
 * that.
 */

const NODE = "rounded-md border border-line bg-surface px-2 py-1 text-center text-2xs text-ink";
const NODE_MUTED = "rounded-md border border-line bg-sunken px-2 py-1 text-center text-2xs text-faint";

function Arrow() {
  return (
    <div className="flex justify-center py-1">
      <ArrowDown className="h-3 w-3 text-faint" />
    </div>
  );
}

export function StageAnimation({ stage, className }: { stage: string; className?: string }) {
  return (
    <div className={cn("rounded-lg border border-line bg-sunken p-3", className)}>
      {body(stage)}
      <p className="mt-2.5 text-center text-2xs italic text-faint">Conceptual diagram</p>
    </div>
  );
}

function body(stage: string) {
  switch (stage) {
    case "query_analysis":
      return (
        <>
          <div className={NODE}>"What is photosynthesis?"</div>
          <Arrow />
          <div className="flex flex-wrap justify-center gap-1">
            {["photosynthesis", "plants", "light"].map((word) => (
              <span key={word} className="rounded border border-line bg-surface px-1.5 py-0.5 text-2xs text-muted">
                {word}
              </span>
            ))}
          </div>
        </>
      );

    case "query_embedding":
      return (
        <>
          <div className={NODE}>your question</div>
          <Arrow />
          <div className={NODE}>embedding model</div>
          <Arrow />
          <div className={NODE_MUTED}>[0.21, -0.73, 0.42, …]</div>
        </>
      );

    case "vector_search":
      return (
        <>
          <div className={NODE}>query vector</div>
          <Arrow />
          <div className="flex items-center justify-center gap-1.5 rounded-md border border-line bg-surface px-2 py-1 text-2xs text-ink">
            <Database className="h-3 w-3" /> vector database
          </div>
          <Arrow />
          <div className="flex justify-center gap-1">
            {[0.92, 0.87, 0.81].map((score) => (
              <span key={score} className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-2xs text-ink">
                {score}
              </span>
            ))}
          </div>
        </>
      );

    case "candidate_retrieval":
      return (
        <>
          <div className="flex justify-center gap-1">
            {[0, 1, 2, 3, 4, 5, 6, 7].map((index) => (
              <span key={index} className="h-5 w-2 rounded-sm bg-line-strong" />
            ))}
          </div>
          <Arrow />
          <div className="text-center text-2xs text-faint">deduplicated, filtered</div>
          <Arrow />
          <div className="flex justify-center gap-1">
            {[0, 1, 2].map((index) => (
              <span key={index} className="h-5 w-2 rounded-sm bg-brand/70" />
            ))}
          </div>
        </>
      );

    case "reranking":
      return (
        <>
          <div className="flex flex-col gap-1">
            {["3", "1", "2"].map((label, index) => (
              <Fragment key={label}>
                <div className={NODE}>chunk {label}</div>
                {index < 2 ? <div className="flex justify-center text-2xs text-faint">↓ reordered</div> : null}
              </Fragment>
            ))}
          </div>
        </>
      );

    case "context_building":
      return (
        <>
          <div className="flex items-center justify-center gap-1 rounded-md border border-line bg-surface px-2 py-1 text-2xs text-ink">
            <FileText className="h-3 w-3" /> relevant chunks
          </div>
          <Arrow />
          <div className={NODE}>
            <span className="font-mono text-brand">[1]</span> … <span className="font-mono text-brand">[2]</span> …
          </div>
          <Arrow />
          <div className={NODE}>context window</div>
        </>
      );

    case "llm_generation":
      return (
        <>
          <div className="grid grid-cols-2 gap-1.5">
            <div className={NODE}>question</div>
            <div className="flex items-center justify-center gap-1 rounded-md border border-line bg-surface px-2 py-1 text-2xs text-ink">
              <Layers className="h-3 w-3" /> context
            </div>
          </div>
          <Arrow />
          <div className={NODE}>language model</div>
          <Arrow />
          <div className={NODE}>answer</div>
        </>
      );

    case "citation_resolution":
      return (
        <>
          <div className={NODE}>
            sentence <span className="font-mono text-brand">[2]</span>
          </div>
          <Arrow />
          <div className={NODE}>Biology Notes · p.12</div>
        </>
      );

    case "grounding":
      return (
        <>
          <div className={NODE}>the answer</div>
          <div className="flex justify-center gap-1 py-1 text-2xs text-faint">compared against</div>
          <div className={NODE}>the evidence</div>
          <Arrow />
          <div className="flex items-center justify-center gap-1 rounded-md border border-line bg-surface px-2 py-1 text-2xs text-ink">
            <Sparkles className="h-3 w-3" /> verdict
          </div>
        </>
      );

    default:
      return <p className="text-center text-2xs text-faint">No diagram for this stage.</p>;
  }
}

export default StageAnimation;
