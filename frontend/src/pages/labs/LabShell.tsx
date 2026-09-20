import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, FlaskConical } from "lucide-react";

import { cn } from "@/lib/cn";
import { Card } from "@/components/ui/Card";

/**
 * Shared chrome for every laboratory page.
 *
 * Each lab answers the same three questions in the same place, so a reader moving
 * between labs always knows where to look:
 *
 *   1. what this stage does and why it exists   -> `concept`
 *   2. what you can change and run              -> `controls`
 *   3. what the REAL system produced            -> `children`
 *
 * The last one is the important one. Every lab is wired to the same code the
 * pipeline uses, and the results panel says which endpoint produced them, so a
 * learner can always tell measured output from explanation.
 */

export interface LabShellProps {
  /** Short title, e.g. "Chunking". */
  title: string;
  /** One line on what the lab demonstrates. */
  tagline: string;
  /** The educational explanation: what/why/without. */
  concept: ReactNode;
  /** Controls for the experiment. */
  controls?: ReactNode;
  /** The real output. */
  children: ReactNode;
  /** Where the numbers came from, shown so nothing looks invented. */
  source?: string;
  className?: string;
}

export function LabShell({
  title,
  tagline,
  concept,
  controls,
  children,
  source,
  className,
}: LabShellProps) {
  return (
    <div className={cn("space-y-4", className)}>
      <div className="flex items-start gap-3">
        <Link
          to="/app/playground"
          className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-surface text-muted transition hover:border-line-strong hover:text-ink"
          aria-label="Back to the RAG Laboratory"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-base font-semibold text-ink">
            <FlaskConical className="h-4 w-4 text-brand" />
            {title}
          </h1>
          <p className="mt-0.5 text-xs leading-relaxed text-muted">{tagline}</p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-4">
          {controls ? <Card padded={false}>{controls}</Card> : null}
          {children}
        </div>

        <aside className="space-y-4">
          <Card padded={false}>
            <div className="border-b border-line px-5 py-3">
              <h2 className="text-xs font-semibold text-ink">What this stage does</h2>
            </div>
            <div className="space-y-2.5 px-5 py-4 text-2xs leading-relaxed text-muted">
              {concept}
            </div>
          </Card>

          {source ? (
            <div className="rounded-xl border border-line bg-sunken px-4 py-3">
              <p className="text-2xs font-medium text-ink">Where these numbers come from</p>
              <p className="mt-1 font-mono text-2xs leading-relaxed text-faint">{source}</p>
            </div>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

/** A labelled row of the explanation: what / why / without. */
export function Concept({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <p className="font-medium text-ink">{label}</p>
      <p className="mt-0.5">{children}</p>
    </div>
  );
}

export default LabShell;
