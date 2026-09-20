import { useMemo } from "react";
import { AlertTriangle, Layers, Loader2, MessageSquareText } from "lucide-react";

import { cn } from "@/lib/cn";
import { formatNumber, formatScore } from "@/lib/format";
import type { ContextBundleOut } from "@/lib/types";
import { useWorkspaces } from "@/state/workspace";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader, Stat } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";
import { AskControls } from "./AskControls";
import { Concept, LabShell } from "./LabShell";
import { useAskRun } from "./useAskRun";

/**
 * Context Laboratory.
 *
 * Answers the question beginners ask most often: "what does the model actually
 * see?" Not the answer, not the sources - the assembled block of numbered
 * evidence that was sent.
 *
 * It is also where the numbering is explained. The `[n]` a reader clicks is
 * created HERE, per question, which is why the same chunk can be [2] in one answer
 * and [5] in another. Without seeing this, that looks like a bug.
 *
 * WHAT IS DELIBERATELY NOT SHOWN
 * ------------------------------
 * The system prompt. It is server-side by design and the API never returns it; the
 * trace is required to contain no hidden prompts. The excerpt block is shown in
 * full because it is the user's own document text, not an internal instruction.
 */
export default function ContextLab() {
  const { activeId } = useWorkspaces();
  const ask = useAskRun(activeId);

  const bundle = useMemo(
    () => (ask.result?.context as unknown as ContextBundleOut | undefined) ?? null,
    [ask.result],
  );

  const utilization = bundle?.budget
    ? Math.min(100, Math.round((bundle.characters / bundle.budget) * 100))
    : 0;

  return (
    <LabShell
      title="Context Laboratory"
      tagline="See exactly what was assembled and sent to the model — and how the [n] numbering is created."
      source="POST /api/chat/ask → response.context (app/rag/context.py :: build_context)"
      concept={
        <>
          <Concept label="What happens">
            The chosen chunks are numbered [1], [2], [3]… and concatenated into one block under a
            character budget, with the question placed after the evidence.
          </Concept>
          <Concept label="Why it exists">
            This is where citations are born. Numbering the evidence here is what lets the model
            cite a source and the UI resolve that citation back to a real chunk.
          </Concept>
          <Concept label="If it were removed">
            The model could still answer, but nothing would connect a sentence to the passage
            supporting it. The citations would be decoration.
          </Concept>
          <Concept label="The budget">
            Context is capped, and chunks that do not fit are counted and reported. A silently
            truncated context would produce a confident answer missing its best evidence.
          </Concept>
          <div className="rounded-lg border border-line bg-sunken px-3 py-2">
            <p className="font-medium text-ink">Why the numbering changes between answers</p>
            <p className="mt-1">
              <span className="font-mono">[n]</span> is assigned per question, not stored on the
              chunk. The same passage can be <span className="font-mono">[2]</span> in one answer
              and <span className="font-mono">[5]</span> in the next. That is expected.
            </p>
          </div>
        </>
      }
      controls={
        <>
          <CardHeader
            title="Ask a question"
            description="The full pipeline runs; this lab shows the context it built."
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

      {bundle ? (
        <>
          <Card padded={false}>
            <CardHeader
              title={`${bundle.excerpt_count} numbered excerpts`}
              description={`"${ask.question}" — answered in ${ask.mode} mode`}
              icon={<Layers className="h-4 w-4" />}
              actions={
                bundle.truncated ? (
                  <Badge tone="caution">truncated to fit</Badge>
                ) : (
                  <Badge tone="positive">fits the budget</Badge>
                )
              }
            />
            <div className="grid grid-cols-2 gap-px bg-line sm:grid-cols-4">
              <Stat label="Excerpts" value={formatNumber(bundle.excerpt_count)} />
              <Stat
                label="Characters"
                value={formatNumber(bundle.characters)}
                hint={`~${formatNumber(Math.round(bundle.characters / 4))} tokens`}
              />
              <Stat label="Budget" value={formatNumber(bundle.budget)} hint={`${utilization}% used`} />
              <Stat
                label="Dropped"
                value={formatNumber(bundle.dropped)}
                hint={bundle.dropped > 0 ? "did not fit" : "nothing was lost"}
              />
            </div>

            {/* budget bar */}
            <div className="border-t border-line px-5 py-4">
              <div className="flex items-baseline justify-between text-2xs">
                <span className="font-medium text-ink">Context budget used</span>
                <span className="font-mono text-faint">
                  {formatNumber(bundle.characters)} / {formatNumber(bundle.budget)}
                </span>
              </div>
              <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-sunken">
                <div
                  className={cn(
                    "h-full rounded-full transition-all duration-500",
                    utilization > 92 ? "bg-caution" : "bg-brand",
                  )}
                  style={{ width: `${utilization}%` }}
                />
              </div>
              {bundle.notes?.length ? (
                <ul className="mt-2 space-y-1">
                  {bundle.notes.map((note, index) => (
                    <li key={index} className="text-2xs leading-relaxed text-faint">
                      {note}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </Card>

          <Card padded={false}>
            <CardHeader
              title="The evidence block, as numbered"
              description="Each excerpt carries the marker the answer will cite. Scores are cosine similarity from retrieval."
            />
            <div className="divide-y divide-line">
              {bundle.excerpts.map((excerpt) => (
                <div key={excerpt.number} className="px-5 py-3.5">
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 inline-flex h-6 min-w-6 shrink-0 items-center justify-center rounded-md border border-brand/30 bg-brand/10 px-1.5 font-mono text-2xs font-medium text-brand">
                      [{excerpt.number}]
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                        <span className="text-2xs font-medium text-ink">{excerpt.label}</span>
                        <span className="font-mono text-2xs text-faint">
                          score {formatScore(excerpt.score)}
                        </span>
                        {excerpt.original_score !== null ? (
                          <span className="font-mono text-2xs text-faint">
                            pre-rerank {formatScore(excerpt.original_score)}
                          </span>
                        ) : null}
                        {excerpt.chunk_index !== null ? (
                          <span className="font-mono text-2xs text-faint">
                            chunk #{excerpt.chunk_index}
                          </span>
                        ) : null}
                      </div>
                      <pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-sunken p-2.5 font-mono text-2xs leading-relaxed text-ink">
                        {excerpt.content}
                      </pre>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <div className="flex items-start gap-2 px-5 py-4">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" />
              <p className="text-2xs leading-relaxed text-muted">
                The system prompt is <strong className="text-ink">not</strong> shown, and the API
                never returns it. It lives server-side only, and the RAG Trace is required to
                contain no hidden prompts. What you see above is your own document text, which is
                the part that determines the answer.
              </p>
            </div>
          </Card>
        </>
      ) : !ask.error && !ask.running ? (
        <Card>
          <EmptyState
            icon={<Layers className="h-5 w-5" />}
            title="No context built yet"
            description="Ask a question and the assembled, numbered evidence will appear here."
          />
        </Card>
      ) : null}
    </LabShell>
  );
}
