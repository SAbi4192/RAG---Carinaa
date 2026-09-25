import { AlertTriangle, FileSearch, Loader2, MessageSquareText, Sparkles } from "lucide-react";

import { cn } from "@/lib/cn";
import { formatDuration, formatNumber } from "@/lib/format";
import { groundingClasses } from "@/lib/format";
import { useWorkspaces } from "@/state/workspace";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader, Stat } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";
import { Markdown } from "@/components/chat/Markdown";
import { AskControls } from "./AskControls";
import { Concept, LabShell } from "./LabShell";
import { useAskRun } from "./useAskRun";

/**
 * Generation Laboratory.
 *
 * Shows the answer together with everything that qualifies it: which provider
 * actually served it, what the grounding check concluded, and which excerpts each
 * citation resolved to.
 *
 * The point is that an answer on its own is not trustworthy. The same sentence is
 * a different claim depending on whether it came from the primary remote engine,
 * from the fallback engine, or from the offline extractive fail-safe - and the
 * UI says which, every time. Cloud vendor identity is never shown (see backend
 * app/core/sanitize.py); only the role is.
 */
export default function GenerationLab() {
  const { activeId } = useWorkspaces();
  const ask = useAskRun(activeId);

  const result = ask.result;
  const grounding = result?.grounding ?? null;
  const citations = result?.citations ?? [];
  const groundingTone = grounding ? groundingClasses(grounding.status) : null;

  return (
    <LabShell
      title="Generation Laboratory"
      tagline="Watch the model answer — and see every qualifier that makes the answer trustworthy, or not."
      source="POST /api/chat/ask → app/llm/ (provider) + app/rag/grounding.py + app/rag/citations.py"
      concept={
        <>
          <Concept label="What happens">
            The question and the numbered evidence are sent to a language model, which writes the
            answer and cites the excerpts it used.
          </Concept>
          <Concept label="Why it exists">
            It is the only stage that writes prose, and the only one that can invent something.
            Everything before it is retrieval; everything after it is verification.
          </Concept>
          <Concept label="If it were removed">
            You would get the raw chunks back — accurate and unreadable as an answer.
          </Concept>
          <Concept label="An answer is not self-justifying">
            The same sentence means different things depending on whether the primary
            engine, the fallback engine,
            or the offline extractive fail-safe produced it. The label below always says which.
          </Concept>
          <div className="rounded-lg border border-line bg-sunken px-3 py-2">
            <p className="font-medium text-ink">Grounding is a separate check from citations</p>
            <p className="mt-1">
              A citation proves a marker points at a real chunk. Grounding asks whether that chunk
              actually <em>supports</em> the claim. Both are needed, and they can disagree.
            </p>
          </div>
        </>
      }
      controls={
        <>
          <CardHeader
            title="Ask a question"
            description="Try the same question in both modes and compare."
            icon={<MessageSquareText className="h-4 w-4" />}
            actions={ask.running ? <Loader2 className="h-3.5 w-3.5 animate-spin text-faint" /> : null}
          />
          <AskControls onRun={ask.run} running={ask.running} />
        </>
      }
    >
      {ask.error ? (
        <Card>
          <div className="px-5 py-4 text-xs text-negative">{ask.error}</div>
        </Card>
      ) : null}

      {result ? (
        <>
          <Card padded={false}>
            <CardHeader
              title={ask.question}
              description={`Answered by ${result.provider_label}`}
              icon={<Sparkles className="h-4 w-4" />}
              actions={
                result.message.used_fallback ? (
                  <Badge tone="caution">fallback</Badge>
                ) : (
                  <Badge tone="positive">{ask.mode}</Badge>
                )
              }
            />
            <div className="grid grid-cols-2 gap-px bg-line sm:grid-cols-4">
              <Stat
                label="Latency"
                value={formatDuration(result.message.latency_ms)}
                hint="end to end"
              />
              <Stat label="Citations" value={formatNumber(citations.length)} hint="resolved" />
              <Stat
                label="Grounding"
                value={grounding ? grounding.status.replace(/_/g, " ").toLowerCase() : "—"}
                hint="evidence check"
              />
              <Stat
                label="Extractive"
                value={result.is_extractive_failsafe ? "yes" : "no"}
                hint={result.is_extractive_failsafe ? "no model used" : "model generated"}
              />
            </div>

            <div className="px-5 py-4">
              <Markdown content={result.answer} />
            </div>

            {result.message.used_fallback ? (
              <div className="flex items-start gap-2 border-t border-line bg-caution/8 px-5 py-3">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-caution" />
                <p className="text-2xs leading-relaxed text-caution">
                  The primary provider was unavailable, so the fallback answered
                  {result.message.fallback_reason ? ` (${result.message.fallback_reason})` : ""}. It
                  is labelled as a fallback rather than presented as the primary — a silent fallback
                  would make answer quality impossible to compare.
                </p>
              </div>
            ) : null}

            {result.is_extractive_failsafe ? (
              <div className="flex items-start gap-2 border-t border-line bg-caution/8 px-5 py-3">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-caution" />
                <p className="text-2xs leading-relaxed text-caution">
                  No language model was used. These are sentences quoted directly from your
                  documents, and nothing was sent to an online provider.
                </p>
              </div>
            ) : null}
          </Card>

          {grounding ? (
            <Card padded={false}>
              <CardHeader
                title="Grounding verdict"
                description={grounding.reason || "The evidence check for this answer."}
                icon={<Sparkles className="h-4 w-4" />}
                actions={
                  <span
                    className={cn(
                      "rounded-full border px-2.5 py-0.5 text-2xs font-medium",
                      groundingTone?.chip,
                    )}
                  >
                    {grounding.status.replace(/_/g, " ")}
                  </span>
                }
              />
              <div className="space-y-2.5 px-5 py-4 text-2xs leading-relaxed">
                <p className="text-muted">
                  Grounding is checked lexically against the retrieved evidence. A low verdict is
                  the system being honest about thin evidence — not a failure. The four verdicts are
                  SUPPORTED, PARTIALLY_SUPPORTED, INSUFFICIENT_EVIDENCE and CITATION_ERROR.
                </p>

                <div className="flex flex-wrap gap-1.5">
                  <Chip label="excerpts" value={String(grounding.excerpt_count)} />
                  <Chip label="cited" value={String(grounding.citation_count)} />
                  <Chip label="top score" value={grounding.top_score.toFixed(3)} />
                  {grounding.invalid_numbers.length > 0 ? (
                    <Chip label="fabricated" value={grounding.invalid_numbers.join(", ")} />
                  ) : null}
                  {grounding.unused_numbers.length > 0 ? (
                    <Chip label="unused excerpts" value={grounding.unused_numbers.join(", ")} />
                  ) : null}
                </div>

                {Object.keys(grounding.counts ?? {}).length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(grounding.counts).map(([key, value]) => (
                      <Chip key={key} label={key.replace(/_/g, " ")} value={String(value)} />
                    ))}
                  </div>
                ) : null}

                {grounding.disclaimer ? (
                  <p className="rounded-lg border border-line bg-sunken px-3 py-2 leading-relaxed text-muted">
                    {grounding.disclaimer}
                  </p>
                ) : null}
              </div>
            </Card>
          ) : null}

          <Card padded={false}>
            <CardHeader
              title={`${citations.length} resolved citations`}
              description="Each [n] in the answer mapped back to the chunk it points at."
              icon={<FileSearch className="h-4 w-4" />}
            />
            {citations.length === 0 ? (
              <EmptyState
                icon={<FileSearch className="h-5 w-5" />}
                title="No citations in this answer"
                description="Either the question was not answerable from the documents, or the model answered without citing."
              />
            ) : (
              <ul className="divide-y divide-line">
                {citations.map((citation) => (
                  <li key={citation.number} className="flex items-start gap-3 px-5 py-3">
                    <span className="mt-0.5 inline-flex h-6 min-w-6 shrink-0 items-center justify-center rounded-md border border-brand/30 bg-brand/10 px-1.5 font-mono text-2xs font-medium text-brand">
                      [{citation.number}]
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-2xs font-medium text-ink">{citation.location_label}</p>
                      {citation.snippet ? (
                        <p className="mt-1 line-clamp-3 text-2xs leading-relaxed text-muted">
                          {citation.snippet}
                        </p>
                      ) : null}
                    </div>
                    <span className="shrink-0 font-mono text-2xs text-faint">
                      {citation.relevance.toFixed(3)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      ) : !ask.error && !ask.running ? (
        <Card>
          <EmptyState
            icon={<Sparkles className="h-5 w-5" />}
            title="No answer yet"
            description="Ask a question and the generated answer, its grounding verdict and its citations will appear here."
          />
        </Card>
      ) : null}
    </LabShell>
  );
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="rounded-md border border-line bg-surface px-2 py-0.5 text-2xs text-muted">
      {label} <span className="font-mono text-ink">{value}</span>
    </span>
  );
}
