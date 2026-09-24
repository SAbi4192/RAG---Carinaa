import { useMemo, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { ChevronDown, Database, Gauge, Globe, BookOpen, Microscope, Sparkles, User } from "lucide-react";

import { cn } from "@/lib/cn";
import { formatDuration, formatScore, formatTimeOfDay, scoreTone } from "@/lib/format";
import type { Citation, Grounding, Language, Message, Variant } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Markdown } from "@/components/chat/Markdown";
import { AnswerToolbar } from "@/components/chat/AnswerToolbar";
import { CitationCard, GroundingBadge } from "@/components/chat/Citations";
import { RefusalPanel } from "@/components/chat/RefusalPanel";

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
  /**
   * When set (Learning Mode), clicking the answer selects it in the right panel so
   * the panel shows THIS message's RAG trace. The callback only exists when the
   * message is an assistant answer in a conversation loaded with Learning Mode on.
   */
  onSelectForLearning?: () => void;
  /** Whether this message's trace is currently shown in the Learning panel. */
  isSelectedForLearning?: boolean;
  /**
   * Offer to re-ask this question with web search enabled.
   *
   * Only wired for assistant answers that did NOT already use the web, and only
   * in online mode. Web search is strictly opt-in: this affordance ENABLES it
   * for the next question, it never fires a web request by itself.
   */
  onExploreWeb?: () => void;
  /** When set, this row carries `[data-question="id"]` for the scroller to anchor. */
  threadAnchor?: number;
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
  onSelectForLearning,
  isSelectedForLearning,
  onExploreWeb,
  threadAnchor,
}: MessageBubbleProps) {
  const isUser = message.role === "user";

  /**
   * Sources are COLLAPSED by default.
   *
   * The answer is the reading experience; five citation cards underneath every
   * reply made the chat feel like a research console. The evidence stays one
   * click away ("View sources"), and clicking a citation marker in the answer
   * still opens and highlights the exact card.
   */
  const [showSources, setShowSources] = useState(false);
  const [showRetrieval, setShowRetrieval] = useState(false);
  const [highlighted, setHighlighted] = useState<number | null>(null);

  /**
   * Learning Mode shows the technical meta badges (latency, retrieval counts).
   * In the normal chat they are noise for a first-time user; the same numbers
   * remain available inside the retrieval detail below.
   */
  const showTechnicalBadges = Boolean(onSelectForLearning);

  const validNumbers = useMemo(
    () => citations.filter((c) => c.kind === "document").map((c) => c.number),
    [citations],
  );

  /**
   * The two kinds of evidence, kept apart deliberately.
   *
   * A reader must never have to guess whether a claim came from their own
   * documents or from the internet, so the sources panel groups them under
   * explicit headings rather than one mixed list.
   */
  const documentCitations = useMemo(
    () => citations.filter((c) => c.kind !== "web"),
    [citations],
  );
  const webCitations = useMemo(
    () => citations.filter((c) => c.kind === "web"),
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
      <div className="flex justify-end gap-3" {...(threadAnchor != null ? { "data-question": threadAnchor } : {})}>
        <div className="max-w-[min(42rem,85%)] animate-fade-up">
          <div className="rounded-2xl rounded-br-md bg-brand px-4 py-2.5 text-sm leading-relaxed text-brand-ink shadow-card">
            <p className="whitespace-pre-wrap">{message.content}</p>
          </div>
          <p className="mt-1 text-right text-2xs text-faint">
            {formatTimeOfDay(message.created_at)}
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

  /**
   * Whether this turn was a refusal rather than an answer.
   *
   * The grounding verdict is the source of truth: INSUFFICIENT_EVIDENCE is exactly the
   * status the backend assigns when retrieval found nothing that supports the question.
   * Deriving it from the verdict rather than from the answer text means the UI cannot
   * disagree with the backend about whether a refusal happened.
   */
  const isRefusal = grounding?.status === "INSUFFICIENT_EVIDENCE";
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

      <div
        className={cn(
          "relative min-w-0 flex-1 animate-fade-up space-y-3 rounded-2xl transition-colors",
          onSelectForLearning && "cursor-pointer",
          // Selected state: a soft tint plus a HAIRLINE ring, never a thick
          // outline drawn across the text. The left pill sits outside the
          // content box entirely, so the message stays fully readable.
          isSelectedForLearning && "bg-brand/[0.05] ring-1 ring-brand/30",
        )}
        onClick={
          onSelectForLearning
            ? (event) => {
                // Let real interactive elements behave normally: a citation marker,
                // a button or a link inside the answer is that element's action, not
                // "select this message" (and selection is harmless either way).
                const target = event.target as HTMLElement;
                if (target.closest("a,button,[role='checkbox'],summary")) {
                  // Still select - showing this answer's trace matches any inspection
                  // intent - but the inner control also handled its own click.
                }
                onSelectForLearning();
              }
            : undefined
        }
        {...(onSelectForLearning
          ? {
              role: "button",
              tabIndex: 0,
              "aria-pressed": isSelectedForLearning,
              "aria-label": "Show this answer's RAG trace in the learning panel",
              onKeyDown: (event: ReactKeyboardEvent) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelectForLearning();
                }
              },
            }
          : {})}
      >
        {/* The selection marker: an outside-the-box pill, like a tab on a page -
            it never crosses the message content. */}
        {isSelectedForLearning ? (
          <span
            aria-hidden
            className="absolute -left-1 top-3 bottom-3 w-1 rounded-full bg-brand"
          />
        ) : null}
        {/* ---- meta row ------------------------------------------- */}
        <div className="flex flex-wrap items-center gap-1.5">
          <ProviderBadge message={message} />

          <GroundingBadge grounding={grounding} compact />

          {/* Where the evidence came from - the first thing a reader should
              know before trusting the answer. */}
          {message.web_search_used ? (
            <Badge tone="accent" icon={<Globe className="h-2.5 w-2.5" />}>
              📚 + 🌐 combined
            </Badge>
          ) : citations.some((citation) => citation.kind === "document") ? (
            <Badge tone="neutral" icon={<BookOpen className="h-2.5 w-2.5" />}>
              📚 from your documents
            </Badge>
          ) : citations.some((citation) => citation.kind === "web") ? (
            <Badge tone="accent" icon={<Globe className="h-2.5 w-2.5" />}>
              🌐 from the web
            </Badge>
          ) : null}

          {onSelectForLearning ? (
            <button
              type="button"
              onClick={onSelectForLearning}
              className={cn(
                "inline-flex items-center gap-1 rounded-lg border px-1.5 py-0.5 text-2xs transition",
                isSelectedForLearning
                  ? "border-brand/40 bg-brand/10 text-brand"
                  : "border-line text-faint hover:border-line-strong hover:text-ink",
              )}
              title={
                isSelectedForLearning
                  ? "This answer's RAG trace is shown in the Learning panel"
                  : "Show this answer's RAG trace in the Learning panel"
              }
            >
              <Microscope className="h-2.5 w-2.5" />
              {isSelectedForLearning ? "Learning selected" : "inspect"}
            </button>
          ) : null}

          {message.latency_ms > 0 && showTechnicalBadges ? (
            <Badge tone="neutral" mono icon={<Gauge className="h-2.5 w-2.5" />}>
              {formatDuration(message.latency_ms)}
            </Badge>
          ) : null}

          {candidateCount > 0 && showTechnicalBadges ? (
            <Badge tone="neutral" mono icon={<Database className="h-2.5 w-2.5" />}>
              {candidateCount} retrieved
            </Badge>
          ) : null}

          {reranked && showTechnicalBadges ? <Badge tone="accent">re-ranked</Badge> : null}
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
              {showSources
                ? "Hide sources"
                : `View sources · ${documentCitations.length} 📚${
                    webCitations.length > 0 ? ` · ${webCitations.length} 🌐` : ""
                  }`}
            </button>

            {showSources ? (
              <div className="mt-2 space-y-2.5">
                {/* 📚 vs 🌐, never mixed into one list. */}
                {documentCitations.length > 0 ? (
                  <div>
                    <p className="mb-1 text-[0.625rem] font-semibold uppercase tracking-wider text-faint">
                      📚 Your documents
                    </p>
                    <div className="grid gap-1.5 sm:grid-cols-2">
                      {documentCitations.map((citation) => (
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
                  </div>
                ) : null}

                {webCitations.length > 0 ? (
                  <div>
                    <p className="mb-1 text-[0.625rem] font-semibold uppercase tracking-wider text-faint">
                      🌐 Web
                    </p>
                    <div className="grid gap-1.5 sm:grid-cols-2">
                      {webCitations.map((citation) => (
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
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : isRefusal ? null : (
          <p className="text-2xs text-faint">
            No sources were cited. Check the grounding verdict above before relying on this.
          </p>
        )}

        {/* ---- optional next step: go beyond the documents ---------
            Web search stays opt-in. This never sends a request itself: it
            arms the web option for the next question and says so. */}
        {onExploreWeb && !message.web_search_used && !isRefusal ? (
          <button
            type="button"
            onClick={onExploreWeb}
            className="inline-flex w-fit items-center gap-1.5 rounded-lg border border-line px-2 py-1 text-2xs font-medium text-faint transition-colors hover:border-line-strong hover:text-ink"
            title="Optionally search the web for more detail on this question. Your documents are always searched first."
          >
            <Globe className="h-3 w-3" />
            Want to explore beyond your documents? Search the web
          </button>
        ) : null}

        {/* A refusal is correct behaviour, but it needs to be actionable. This
            reports what actually happened - real counts, not a guess about the cause -
            and suggests the specific next step. */}
        {isRefusal ? (
          <RefusalPanel message={message} grounding={grounding} />
        ) : null}

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
