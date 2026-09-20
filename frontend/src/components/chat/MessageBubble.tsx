import { useMemo, useState } from "react";
import { ChevronDown, Database, Gauge, Sparkles, User } from "lucide-react";

import { cn } from "@/lib/cn";
import { formatDuration, formatScore, scoreTone } from "@/lib/format";
import type { Citation, Grounding, Language, Message, Variant } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Markdown } from "@/components/chat/Markdown";
import { AnswerToolbar } from "@/components/chat/AnswerToolbar";
import { CitationCard, GroundingBadge } from "@/components/chat/Citations";

/**
 * One turn in a conversation.
 *
 * The assistant bubble carries more than the answer, and each extra piece is
 * there for a reason:
 *
 *   PROVIDER      which model actually answered. A Groq fallback is labelled as a
 *                 fallback - never disguised as the primary. That is a hard rule.
 *   GROUNDING     how well the evidence supports the answer.
 *   LATENCY       how long it really took, including the slow local model.
 *   CITATIONS     the sources, with real page/slide/sheet locations.
 *   RETRIEVAL     the scores behind the answer, collapsed by default but one click
 *                 away. This is what makes the system auditable rather than magic.
 */

export interface MessageBubbleProps {
  message: Message;
  grounding: Grounding | null;
  citations: Citation[];
  languages: Language[];
  activeVariant: Variant | null;
  onVariantChange: (variant: Variant | null) => void;
  onOpenTrace?: () => void;
  defaultLanguage?: string;
}

export function MessageBubble({
  message,
  grounding,
  citations,
  languages,
  activeVariant,
  onVariantChange,
  onOpenTrace,
  defaultLanguage,
}: MessageBubbleProps) {
  const isUser = message.role === "user";

  const [showSources, setShowSources] = useState(true);
  const [showRetrieval, setShowRetrieval] = useState(false);
  const [highlighted, setHighlighted] = useState<number | null>(null);

  const validNumbers = useMemo(
    () => citations.filter((c) => c.kind === "document").map((c) => c.number),
    [citations],
  );

  const displayedContent = activeVariant?.content ?? message.content;

  /**
   * Whether this was the offline extractive fail-safe.
   *
   * `is_extractive_failsafe` is not a field on `Message` - the backend sets it only
   * at the top level of `AskResponse`. So the single source of truth per message is
   * the marker the pipeline writes into `model`: it sets `model = "extractive"`
   * exactly when the offline fail-safe produced the answer, and that value is
   * persisted on the row. Deriving from it works both on `/chat/ask` and after a
   * reload, so there is nothing to reconstruct and nothing to disagree about.
   *
   * Getting this wrong would display a quoted answer as if a language model had
   * generated it - exactly the kind of misattribution this project must not make.
   */
  const isFailsafe = message.model === "extractive";

  /* ---- user turn ------------------------------------------------------ */
  if (isUser) {
    return (
      <div className="flex justify-end gap-3">
        <div className="max-w-[min(42rem,85%)] animate-fade-up">
          <div className="rounded-2xl rounded-br-md bg-brand px-4 py-2.5 text-sm leading-relaxed text-brand-ink shadow-card">
            <p className="whitespace-pre-wrap">{message.content}</p>
          </div>
          <p className="mt-1 text-right text-2xs text-faint">
            {new Date(message.created_at).toLocaleTimeString(undefined, {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </p>
        </div>
        <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-line bg-surface text-faint">
          <User className="h-3.5 w-3.5" />
        </span>
      </div>
    );
  }

  /* ---- assistant turn ------------------------------------------------- */
  const retrieval = message.retrieval ?? {};
  // `candidates_retrieved` is the field RetrievalOutcome.as_dict() actually emits;
  // `candidates` is kept as a fallback because the retrieval-only endpoint has used
  // that name. Reading only `candidates` made this count permanently 0, so the
  // panel reported "Candidates 0" for every answer that had any at all.
  const candidateCount = Number(
    retrieval.candidates_retrieved ?? retrieval.candidates ?? 0,
  );
  const topScore = Number(retrieval.top_score ?? 0);
  const meanScore = Number(retrieval.mean_score ?? 0);
  const reranked = Boolean(retrieval.reranked);

  /**
   * The scope this answer actually used.
   *
   * Read from the stored retrieval payload rather than recomputed, because the
   * chat scope can change after the answer was given. Showing today's scope next
   * to yesterday's answer would tell the reader it searched documents it never
   * saw.
   */
  const scopeKind = retrieval.retrieval_scope ? String(retrieval.retrieval_scope) : null;
  const scopeSearched = Number(retrieval.workspace_documents_searched ?? 0);
  const scopeAvailable = Number(retrieval.workspace_documents_available ?? 0);

  return (
    <div className="flex gap-3">
      <span
        className={cn(
          "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border",
          isFailsafe
            ? "border-caution/30 bg-caution/10 text-caution"
            : "border-brand/25 bg-brand/10 text-brand",
        )}
      >
        <Sparkles className="h-3.5 w-3.5" />
      </span>

      <div className="min-w-0 flex-1 animate-fade-up space-y-3">
        {/* ---- meta row ------------------------------------------- */}
        <div className="flex flex-wrap items-center gap-1.5">
          <ProviderBadge message={message} />

          <GroundingBadge grounding={grounding} compact />

          {message.latency_ms > 0 ? (
            <Badge tone="neutral" mono icon={<Gauge className="h-2.5 w-2.5" />}>
              {formatDuration(message.latency_ms)}
            </Badge>
          ) : null}

          {candidateCount > 0 ? (
            <Badge tone="neutral" mono icon={<Database className="h-2.5 w-2.5" />}>
              {candidateCount} retrieved
            </Badge>
          ) : null}

          {reranked ? <Badge tone="accent">re-ranked</Badge> : null}
        </div>

        {/* ---- failsafe warning ----------------------------------- */}
        {isFailsafe ? (
          <div className="rounded-lg border border-caution/30 bg-caution/8 px-3 py-2">
            <p className="text-2xs font-medium text-caution">
              Extractive answer - no language model was used
            </p>
            <p className="mt-1 text-2xs leading-relaxed text-muted">
              The local model was unavailable, so these are sentences quoted directly from
              your documents. Nothing was sent to an online provider. This is the offline
              fail-safe, not a generated answer.
            </p>
          </div>
        ) : null}

        {/* ---- answer --------------------------------------------- */}
        <div className="rounded-2xl rounded-tl-md border border-line bg-surface px-4 py-3 shadow-card">
          <Markdown
            content={displayedContent}
            validCitationNumbers={validNumbers}
            onCitationClick={(number) => {
              setShowSources(true);
              setHighlighted(number);
              window.setTimeout(() => setHighlighted(null), 2200);
              document
                .getElementById(`citation-${message.id}-${number}`)
                ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
            }}
          />
        </div>

        {/* ---- toolbar ------------------------------------------- */}
        <AnswerToolbar
          message={message}
          displayedContent={displayedContent}
          citations={citations}
          activeVariant={activeVariant}
          onVariantChange={onVariantChange}
          onOpenTrace={onOpenTrace}
          languages={languages}
          defaultLanguage={defaultLanguage}
        />

        {/* ---- sources ------------------------------------------- */}
        {citations.length > 0 ? (
          <div>
            <button
              type="button"
              onClick={() => setShowSources((value) => !value)}
              aria-expanded={showSources}
              className="flex items-center gap-1.5 text-2xs font-medium text-muted transition-colors hover:text-ink"
            >
              <ChevronDown
                className={cn("h-3 w-3 transition-transform", showSources && "rotate-180")}
              />
              {citations.length} source{citations.length === 1 ? "" : "s"}
            </button>

            {showSources ? (
              <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                {citations.map((citation) => (
                  <div
                    key={`${citation.kind}-${citation.number}`}
                    id={`citation-${message.id}-${citation.number}`}
                    className={cn(
                      "rounded-lg transition-all duration-300",
                      highlighted === citation.number &&
                        "ring-2 ring-brand/50 ring-offset-1 ring-offset-canvas",
                    )}
                  >
                    <CitationCard citation={citation} />
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        ) : (
          <p className="text-2xs text-faint">
            No sources were cited. Check the grounding verdict above before relying on this.
          </p>
        )}

        {/* ---- retrieval detail ---------------------------------- */}
        {candidateCount > 0 || topScore > 0 ? (
          <div>
            <button
              type="button"
              onClick={() => setShowRetrieval((value) => !value)}
              aria-expanded={showRetrieval}
              className="flex items-center gap-1.5 text-2xs font-medium text-muted transition-colors hover:text-ink"
            >
              <ChevronDown
                className={cn("h-3 w-3 transition-transform", showRetrieval && "rotate-180")}
              />
              Retrieval detail
            </button>

            {showRetrieval ? (
              <div className="mt-2 animate-slide-down rounded-lg border border-line bg-sunken px-3.5 py-3">
                <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
                  <Metric label="Candidates" value={String(candidateCount)} />
                  <Metric
                    label="Top score"
                    value={formatScore(topScore)}
                    valueClass={scoreTone(topScore)}
                  />
                  <Metric
                    label="Excerpts used"
                    value={String(
                      Number((message.grounding_detail as Record<string, unknown>)?.excerpt_count ?? 0) ||
                        citations.length,
                    )}
                  />
                  <Metric label="Re-ranked" value={reranked ? "yes" : "no"} />
                </dl>

                {/*
                  Candidate Retrieval, broken down.
                  These are the same numbers the RAG Trace reports for the
                  `candidate_retrieval` stage - the retrieval step that turns a wide
                  recall pool into the few excerpts the model actually sees. Showing
                  them here answers "how was this found?" without leaving the chat.
                */}
                {/* What was searched. Placed first, because every number below
                    is only meaningful relative to the scope. */}
                {scopeKind ? (
                  <p className="mt-2.5 border-t border-line pt-2.5 text-2xs leading-relaxed text-muted">
                    <span className="font-medium text-ink">Scope: </span>
                    {scopeKind === "chat"
                      ? `searched ${scopeSearched} of ${scopeAvailable} workspace document(s) — only those selected for this chat`
                      : `searched the whole workspace (${scopeAvailable} document(s))`}
                  </p>
                ) : null}

                <div className="mt-2.5 border-t border-line pt-2.5">
                  <p className="text-2xs font-medium text-ink">Candidate retrieval</p>
                  <dl className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
                    <Metric label="Candidates returned" value={String(candidateCount)} />
                    <Metric
                      label="Duplicates removed"
                      value={String(Number(retrieval.duplicates_removed ?? 0))}
                    />
                    <Metric
                      label="Below threshold"
                      value={String(Number(retrieval.below_threshold ?? 0))}
                    />
                    <Metric
                      label="Final excerpts"
                      value={String(Number(retrieval.returned ?? citations.length))}
                    />
                    <Metric label="Distance" value="cosine" />
                    {typeof meanScore === "number" && meanScore > 0 ? (
                      <Metric label="Mean score" value={formatScore(meanScore)} />
                    ) : null}
                  </dl>
                </div>

                {typeof retrieval.threshold === "number" ? (
                  <p className="mt-2.5 border-t border-line pt-2 text-2xs text-faint">
                    Similarity threshold applied: {formatScore(retrieval.threshold)}. Candidates
                    below it were discarded before context building.
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */

/**
 * Provider label.
 *
 * The single most important honesty rule in the UI: if the answer came from the
 * fallback, it says so. There is no configuration in which a Groq response is
 * displayed as "Gemini".
 */
function ProviderBadge({ message }: { message: Message }) {
  const isLocal = message.provider === "local";
  const isFallback = message.used_fallback;
  // Same derivation as in the bubble: `is_extractive_failsafe` is not on `Message`,
  // so the pipeline's durable `extractive` model marker is the signal.
  const isFailsafe = message.model === "extractive";

  const label = isFailsafe
    ? "Extractive (no model)"
    : isLocal
      ? message.model
        ? `Local · ${message.model}`
        : "Local model"
      : isFallback
        ? `${capitalize(message.provider)} · Fallback`
        : capitalize(message.provider || "unknown");

  return (
    <span title={message.fallback_reason || undefined}>
      <Badge
        tone={isFailsafe ? "caution" : isFallback ? "caution" : isLocal ? "accent" : "brand"}
        icon={isFallback ? <span className="text-2xs">↳</span> : undefined}
      >
        {label}
      </Badge>
    </span>
  );
}

function Metric({
  label,
  value,
  valueClass,
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div>
      <dt className="text-2xs uppercase tracking-wide text-faint">{label}</dt>
      <dd className={cn("mt-0.5 font-mono text-xs tabular-nums text-ink", valueClass)}>{value}</dd>
    </div>
  );
}

function capitalize(value: string): string {
  if (!value) return "Unknown";
  return value.charAt(0).toUpperCase() + value.slice(1);
}
