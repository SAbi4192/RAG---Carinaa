import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ChevronDown,
  ExternalLink,
  FileSpreadsheet,
  FileText,
  FileType2,
  FileJson,
  Presentation,
  Quote,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { fileAccent, fileBadge, formatScore, scoreTone } from "@/lib/format";
import type { Citation, Grounding } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { groundingClasses } from "@/lib/format";

/**
 * Citation and grounding presentation.
 *
 * These two components are where the project's central claim becomes visible to a
 * user, so they are worth being precise about:
 *
 *  - A citation shows the REAL location. If the chunk came from page 32 of a PDF,
 *    it says "p.32". If it came from row 14 of a sheet called Enrolment, it says
 *    exactly that. The provenance keys are not decoration; they are the product.
 *
 *  - The relevance score is shown, not hidden. A source that matched at 0.31 is
 *    visibly weaker than one at 0.82, and pretending otherwise would overstate how
 *    well-evidenced the answer is.
 *
 *  - The grounding badge states its own limitation. "Supported" means the evidence
 *    backs the answer - not that the answer is true. The evidence itself could be
 *    wrong. Saying so is the honest thing to do and it is cheap to do.
 */

function fileIcon(filename: string) {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  const props = { className: "h-3.5 w-3.5" };
  if (ext === "pdf") return <FileText {...props} />;
  if (ext === "docx" || ext === "doc") return <FileType2 {...props} />;
  if (ext === "pptx" || ext === "ppt") return <Presentation {...props} />;
  if (["xlsx", "xls", "csv"].includes(ext)) return <FileSpreadsheet {...props} />;
  if (ext === "json" || ext === "jsonl") return <FileJson {...props} />;
  return <FileText {...props} />;
}

/* -------------------------------------------------------------------------- */
/* Grounding badge                                                             */
/* -------------------------------------------------------------------------- */

export function GroundingBadge({
  grounding,
  compact,
  className,
}: {
  grounding: Grounding | null;
  compact?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);

  if (!grounding) {
    return (
      <Badge tone="neutral" className={className}>
        not checked
      </Badge>
    );
  }

  const styles = groundingClasses(grounding.status);

  const badge = (
    <button
      type="button"
      onClick={() => setOpen((value) => !value)}
      aria-expanded={open}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-2xs font-medium transition-all",
        styles.chip,
        "hover:brightness-105",
        className,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", styles.dot)} />
      {grounding.label || styles.label}
      {!compact ? (
        <ChevronDown className={cn("h-3 w-3 transition-transform", open && "rotate-180")} />
      ) : null}
    </button>
  );

  if (compact) {
    return <span title={grounding.reason || styles.description}>{badge}</span>;
  }

  return (
    <div className="inline-block">
      {badge}

      {open ? (
        <div className="mt-2 w-full max-w-md animate-slide-down rounded-lg border border-line bg-raised p-3 shadow-pop">
          <p className="text-2xs leading-relaxed text-muted">{styles.description}</p>

          {grounding.reason ? (
            <p className="mt-2 rounded border border-line bg-sunken px-2.5 py-1.5 text-2xs leading-relaxed text-ink">
              {grounding.reason}
            </p>
          ) : null}

          <div className="mt-2.5 flex flex-wrap gap-1.5">
            <Badge tone="neutral" mono>
              {grounding.citation_count} cited
            </Badge>
            <Badge tone="neutral" mono>
              {grounding.excerpt_count} retrieved
            </Badge>
            {grounding.top_score ? (
              <Badge tone="neutral" mono>
                top {formatScore(grounding.top_score)}
              </Badge>
            ) : null}
            {grounding.unused_numbers.length > 0 ? (
              <Badge tone="neutral" mono>
                {grounding.unused_numbers.length} unused
              </Badge>
            ) : null}
          </div>

          {/* Named checks, straight from the backend. Shown because "supported"
              should be inspectable rather than a black box. */}
          {grounding.checks && Object.keys(grounding.checks).length > 0 ? (
            <ul className="mt-2.5 space-y-1 border-t border-line pt-2.5">
              {Object.entries(grounding.checks).map(([name, check]) => {
                const passed =
                  typeof check === "object" && check !== null
                    ? Boolean((check as Record<string, unknown>).passed)
                    : Boolean(check);
                const detail =
                  typeof check === "object" && check !== null
                    ? String((check as Record<string, unknown>).detail ?? "")
                    : "";
                return (
                  <li key={name} className="flex items-start gap-2">
                    <span
                      className={cn(
                        "mt-1 h-1.5 w-1.5 shrink-0 rounded-full",
                        passed ? "bg-positive" : "bg-caution",
                      )}
                    />
                    <span className="min-w-0">
                      <span className="font-mono text-2xs text-ink">
                        {name.replace(/_/g, " ")}
                      </span>
                      {detail ? (
                        <span className="block text-2xs leading-relaxed text-faint">{detail}</span>
                      ) : null}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : null}

          {grounding.disclaimer ? (
            <p className="mt-2.5 border-t border-line pt-2 text-2xs leading-relaxed text-faint">
              {grounding.disclaimer}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Citation card                                                               */
/* -------------------------------------------------------------------------- */

export function CitationCard({
  citation,
  className,
}: {
  citation: Citation;
  className?: string;
}) {
  const [expanded, setExpanded] = useState(false);

  if (citation.kind === "web") {
    return (
      <a
        href={citation.url}
        target="_blank"
        rel="noopener noreferrer"
        className={cn(
          "flex items-start gap-3 rounded-lg border border-line bg-surface px-3 py-2.5 transition-all hover:border-line-strong hover:shadow-card",
          className,
        )}
      >
        <span className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded bg-info/15 font-mono text-2xs font-semibold text-info">
          W{citation.number}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-2xs font-medium text-ink">{citation.title || citation.url}</p>
          <p className="truncate text-2xs text-faint">{citation.url}</p>
        </div>
        <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 text-faint" />
      </a>
    );
  }

  const hasDetail =
    citation.section ||
    citation.page_number ||
    citation.slide_number ||
    citation.sheet_name ||
    citation.json_path;

  return (
    <div
      className={cn(
        "rounded-lg border border-line bg-surface transition-all duration-200 hover:border-line-strong",
        className,
      )}
    >
      <div className="flex items-start gap-3 px-3 py-2.5">
        <span className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded bg-brand/15 font-mono text-2xs font-semibold text-brand">
          {citation.number}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className={cn("shrink-0", fileAccent(citation.document_name).split(" ")[0])}>
              {fileIcon(citation.document_name)}
            </span>
            <p className="truncate text-2xs font-medium text-ink">
              {citation.document_name || `Document ${citation.document_id ?? "?"}`}
            </p>
            <Badge tone="neutral" mono className="shrink-0">
              {fileBadge(citation.document_name)}
            </Badge>
          </div>

          {/* The location line: this is the "traceable" part. */}
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="font-mono text-2xs text-muted">{citation.location_label}</span>
            {citation.relevance > 0 ? (
              <span className={cn("font-mono text-2xs", scoreTone(citation.relevance))}>
                {formatScore(citation.relevance)}
              </span>
            ) : null}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          {citation.document_id ? (
            /* Opens the document ON this exact chunk: the viewer jumps to the
               right page and pulses the passage, so the reader never has to hunt
               for the evidence the citation is pointing at. Without the chunk id
               it degrades to opening the document itself, which is still honest -
               the link never claims to show a passage it cannot locate. */
            <Link
              to={`/app/knowledge/${citation.document_id}${
                citation.chunk_id ? `?chunk=${citation.chunk_id}` : ""
              }`}
              className="rounded p-1 text-faint transition-colors hover:text-brand"
              title="Open the exact passage this citation came from"
              aria-label="Open source passage in the document viewer"
            >
              <ExternalLink className="h-3 w-3" />
            </Link>
          ) : null}
          {citation.snippet ? (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              aria-label={expanded ? "Hide the quoted passage" : "Show the quoted passage"}
              className="rounded p-1 text-faint transition-colors hover:text-ink"
            >
              <Quote className="h-3 w-3" />
            </button>
          ) : null}
        </div>
      </div>

      {expanded && citation.snippet ? (
        <div className="animate-slide-down border-t border-line px-3 py-2.5">
          <p className="text-2xs font-medium uppercase tracking-wide text-faint">
            Retrieved passage
          </p>
          <p className="mt-1.5 whitespace-pre-wrap text-2xs leading-relaxed text-muted">
            {citation.snippet}
          </p>
          {citation.chunk_index !== null && citation.chunk_index !== undefined ? (
            <p className="mt-2 font-mono text-2xs text-faint">
              chunk #{citation.chunk_index}
              {citation.chunk_id ? ` · id ${citation.chunk_id}` : ""}
              {hasDetail && citation.rank ? ` · rank ${citation.rank}` : ""}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** A compact row of citation chips, for use under a short answer. */
export function CitationStrip({
  citations,
  className,
}: {
  citations: Citation[];
  className?: string;
}) {
  if (!citations.length) return null;
  return (
    <div className={cn("flex flex-wrap gap-1.5", className)}>
      {citations.map((citation) => (
        <span
          key={`${citation.kind}-${citation.number}`}
          title={citation.location_label}
          className="inline-flex items-center gap-1 rounded border border-line bg-sunken px-1.5 py-0.5 font-mono text-2xs text-muted"
        >
          <span className="font-semibold text-brand">{citation.marker}</span>
          <span className="max-w-[12rem] truncate">{citation.location_label}</span>
        </span>
      ))}
    </div>
  );
}
