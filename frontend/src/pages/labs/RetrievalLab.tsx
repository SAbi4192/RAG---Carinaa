import { useCallback, useMemo, useState } from "react";
import { ArrowRight, Info, ListChecks, Loader2, ScanSearch, Sparkles, Target } from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api } from "@/lib/api";
import { formatNumber, formatScore } from "@/lib/format";
import type { RetrievalOutcome, RetrievedChunk } from "@/lib/types";
import { useAsync } from "@/hooks/useAsync";
import { useWorkspaces } from "@/state/workspace";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, Stat } from "@/components/ui/Card";
import { Input, Select, Toggle } from "@/components/ui/Field";
import { EmptyState, Spinner } from "@/components/ui/Feedback";
import { RagPipeline } from "@/components/rag/RagPipeline";
import { Concept, LabShell } from "./LabShell";

/**
 * Retrieval Laboratory.
 *
 * Generation is switched off on purpose. This lab shows the half of the pipeline
 * that is hardest to reason about and easiest to blame wrongly: if the chunks are
 * wrong, no amount of prompting will save the answer, and if the chunks are right,
 * a bad answer is the model's fault and not retrieval's.
 *
 * Running the full pipeline hides that distinction behind an answer that may look
 * fine anyway.
 *
 * The two-number design is the thing to experiment with here:
 *   candidate_k = RECALL    (cast a wide net)
 *   top_k       = PRECISION (keep the best few)
 * Widening candidate_k and getting the same chunks back is proof that the problem
 * is upstream, in chunking or embedding - not in the ranking.
 */

const EXAMPLES = [
  "How does virtualization improve resource utilization?",
  "What is a hypervisor?",
  "limitations of virtualization",
  "container versus virtual machine",
];

export default function RetrievalLab() {
  const { activeId, loading: workspacesLoading } = useWorkspaces();

  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [topK, setTopK] = useState(5);
  const [candidateK, setCandidateK] = useState(20);
  const [useRerank, setUseRerank] = useState(false);
  const [documentFilter, setDocumentFilter] = useState("");

  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<RetrievalOutcome | null>(null);
  const [stages, setStages] = useState<Parameters<typeof RagPipeline>[0]["stages"]>([]);
  const [totalMs, setTotalMs] = useState(0);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);

  const documents = useAsync(
    activeId ? () => api.documents.list(activeId) : null,
    [activeId],
  );
  const readyDocs = useMemo(
    () => (documents.data?.documents ?? []).filter((d) => d.status === "ready"),
    [documents.data],
  );

  const run = useCallback(
    async (query: string) => {
      const trimmed = query.trim();
      if (!trimmed || !activeId) return;

      setRunning(true);
      setError("");
      try {
        const response = await api.chat.retrieve({
          workspace_id: activeId,
          question: trimmed,
          top_k: topK,
          candidate_k: candidateK,
          use_rerank: useRerank,
          document_ids: documentFilter ? [Number(documentFilter)] : undefined,
        });

        const retrieval = response.retrieval as RetrievalOutcome;
        setOutcome(retrieval);

        // The trace comes from the same endpoint, so the pipeline shown here is
        // the pipeline that produced these chunks - not a re-enactment.
        const trace = response.trace as { stages?: typeof stages; total_ms?: number };
        setStages(trace?.stages ?? []);
        setTotalMs(trace?.total_ms ?? 0);
      } catch (cause) {
        setError(cause instanceof ApiError ? cause.message : "Retrieval failed.");
        setOutcome(null);
        setStages([]);
      } finally {
        setRunning(false);
      }
    },
    [activeId, topK, candidateK, useRerank, documentFilter],
  );

  if (workspacesLoading) {
    return (
      <LabShell title="Retrieval Laboratory" tagline="Loading…" concept={null}>
        <Card>
          <div className="px-5 py-10 text-center">
            <Spinner className="mx-auto" />
          </div>
        </Card>
      </LabShell>
    );
  }

  const chunks = outcome?.chunks ?? [];

  return (
    <LabShell
      title="Retrieval Laboratory"
      tagline="Search the vector store with generation switched off, so you can judge the evidence on its own."
      source="POST /api/chat/retrieve → app/rag/retriever.py (no language model is called)"
      concept={
        <>
          <Concept label="What happens">
            The question is embedded, compared against every stored vector, and the closest
            candidates are returned with their scores.
          </Concept>
          <Concept label="Why it exists">
            Retrieval decides what the model is allowed to know. If the right passage is not in
            this list, the answer cannot contain it — no prompt can recover evidence that was never
            retrieved.
          </Concept>
          <Concept label="If it were removed">
            The model would answer from its own memory, and the citations would be decoration.
          </Concept>
          <Concept label="Two numbers, two jobs">
            <span className="font-mono text-ink">candidate_k</span> is recall — how wide the net is
            cast. <span className="font-mono text-ink">top_k</span> is precision — how many survive
            into the context.
          </Concept>
          <div className="rounded-lg border border-line bg-sunken px-3 py-2">
            <p className="font-medium text-ink">The diagnostic to remember</p>
            <p className="mt-1">
              Widen <span className="font-mono">candidate_k</span> a lot. If the same chunks come
              back, ranking is not your problem — chunking or embedding is.
            </p>
          </div>
        </>
      }
      controls={
        <>
          <CardHeader
            title="Retrieval controls"
            description="No language model runs, so every number below is a retrieval measurement."
            icon={<ScanSearch className="h-4 w-4" />}
            actions={running ? <Loader2 className="h-3.5 w-3.5 animate-spin text-faint" /> : null}
          />
          <div className="space-y-3.5 p-5">
            <Input
              label="Question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void run(question);
              }}
              placeholder="Ask something…"
            />

            <div className="flex flex-wrap gap-1.5">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => {
                    setQuestion(example);
                    void run(example);
                  }}
                  className="rounded-lg border border-line bg-surface px-2 py-1 text-2xs text-muted transition hover:border-line-strong hover:text-ink"
                >
                  {example}
                </button>
              ))}
            </div>

            <div className="grid gap-3.5 sm:grid-cols-2">
              <Input
                label="candidate_k (recall)"
                type="number"
                min={1}
                max={100}
                value={candidateK}
                onChange={(event) => setCandidateK(Number(event.target.value) || 20)}
                hint="Neighbours fetched before filtering."
              />
              <Input
                label="top_k (precision)"
                type="number"
                min={1}
                max={20}
                value={topK}
                onChange={(event) => setTopK(Number(event.target.value) || 5)}
                hint="How many reach the context."
              />
            </div>

            {readyDocs.length > 1 ? (
              <Select
                label="Limit to one document"
                value={documentFilter}
                onChange={(event) => setDocumentFilter(event.target.value)}
                options={[
                  { value: "", label: "All documents in this workspace" },
                  ...readyDocs.map((doc) => ({
                    value: String(doc.id),
                    label: doc.original_filename,
                  })),
                ]}
              />
            ) : null}

            <div className="border-t border-line pt-3.5">
              <Toggle
                checked={useRerank}
                onChange={setUseRerank}
                label="Re-rank candidates"
                hint="Off by default. Re-ranking reorders what retrieval already found — it cannot add anything."
              />
            </div>

            <Button className="w-full" onClick={() => void run(question)} loading={running}>
              <Target className="h-3.5 w-3.5" />
              Run retrieval
            </Button>
          </div>
        </>
      }
    >
      {error ? (
        <Card>
          <div className="px-5 py-4 text-xs text-negative">{error}</div>
        </Card>
      ) : null}

      {outcome ? (
        <>
          <Card padded={false}>
            <CardHeader
              title="Retrieval result"
              description={`"${outcome.query}"`}
              icon={<ScanSearch className="h-4 w-4" />}
              actions={
                outcome.reranked ? (
                  <Badge tone="accent">re-ranked</Badge>
                ) : (
                  <Badge tone="neutral">not re-ranked</Badge>
                )
              }
            />
            <div className="grid grid-cols-2 gap-px bg-line sm:grid-cols-4">
              <Stat label="Candidates" value={formatNumber(outcome.candidates)} hint="recall pool" />
              <Stat label="Returned" value={formatNumber(outcome.returned)} hint={`top_k = ${topK}`} />
              <Stat
                label="Top score"
                value={formatScore(outcome.top_score)}
                hint={outcome.top_score < 0.4 ? "low — weak match" : "cosine similarity"}
              />
              <Stat
                label="Re-ranked"
                value={outcome.reranked ? "yes" : "no"}
                hint={outcome.rerank_reason ? outcome.rerank_reason.slice(0, 40) : "—"}
              />
            </div>
            {outcome.top_score < 0.4 ? (
              <div className="flex items-start gap-2 border-t border-line bg-caution/8 px-5 py-3">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-caution" />
                <p className="text-2xs leading-relaxed text-caution">
                  The best score is {formatScore(outcome.top_score)}, which is low. That usually
                  means this workspace does not cover the question — not that the search is broken.
                  The system will still answer, and grounding should report{" "}
                  <span className="font-mono">INSUFFICIENT_EVIDENCE</span>.
                </p>
              </div>
            ) : null}
          </Card>

          <Card padded={false}>
            <CardHeader
              title={`${chunks.length} chunks, ranked`}
              description="Ordered by relevance. Click one to read the stored text."
              icon={<ListChecks className="h-4 w-4" />}
            />
            {chunks.length === 0 ? (
              <EmptyState
                icon={<ScanSearch className="h-5 w-5" />}
                title="Nothing retrieved"
                description="No chunk in this workspace is close enough to the question."
              />
            ) : (
              <ol className="divide-y divide-line">
                {chunks.map((chunk, index) => (
                  <ChunkRow
                    key={`${chunk.vector_id}-${index}`}
                    chunk={chunk}
                    position={index + 1}
                    survivors={topK}
                    open={expanded === index}
                    onToggle={() => setExpanded(expanded === index ? null : index)}
                  />
                ))}
              </ol>
            )}
          </Card>

          {stages.length > 0 ? (
            <Card padded={false}>
              <CardHeader
                title="What retrieval actually did"
                description="Measured stages from this run. Click any stage for what it does and why."
                icon={<Sparkles className="h-4 w-4" />}
              />
              <div className="px-5 py-4">
                <RagPipeline stages={stages} totalMs={totalMs} animate />
              </div>
            </Card>
          ) : null}
        </>
      ) : !error && !running ? (
        <Card>
          <EmptyState
            icon={<ScanSearch className="h-5 w-5" />}
            title="Nothing retrieved yet"
            description="Ask a question above and the real retriever will run."
          />
        </Card>
      ) : null}
    </LabShell>
  );
}

function ChunkRow({
  chunk,
  position,
  survivors,
  open,
  onToggle,
}: {
  chunk: RetrievedChunk;
  position: number;
  survivors: number;
  open: boolean;
  onToggle: () => void;
}) {
  const survives = position <= survivors;
  const section = chunk.metadata?.section as string | undefined;
  const page = chunk.metadata?.page_number as number | undefined;
  const moved =
    chunk.original_rank !== null &&
    chunk.original_rank !== undefined &&
    chunk.original_rank !== chunk.rank;

  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-5 py-3 text-left transition hover:bg-raised"
      >
        <span className="w-6 shrink-0 font-mono text-2xs text-faint">{position}</span>

        <span
          className={cn(
            "h-6 w-1.5 shrink-0 rounded-full",
            survives ? "bg-positive/70" : "bg-line-strong",
          )}
          aria-hidden
        />

        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs text-ink">
            {chunk.content.slice(0, 100).replace(/\s+/g, " ")}
          </span>
          <span className="mt-0.5 block text-2xs text-faint">
            {chunk.document_name}
            {section ? ` · ${section}` : ""}
            {page ? ` · p.${page}` : ""}
          </span>
        </span>

        {moved ? (
          <span className="shrink-0 font-mono text-2xs text-accent" title="rank changed by re-ranking">
            {chunk.original_rank}
            <ArrowRight className="inline h-2.5 w-2.5" />
            {chunk.rank}
          </span>
        ) : null}

        <span className="shrink-0 font-mono text-2xs text-ink">{formatScore(chunk.score)}</span>

        {survives ? (
          <Badge tone="positive">in context</Badge>
        ) : (
          <Badge tone="neutral">dropped</Badge>
        )}
      </button>

      {open ? (
        <div className="animate-fade-up space-y-2 border-t border-line bg-sunken px-5 py-4">
          <div className="flex flex-wrap gap-1.5 text-2xs">
            <span className="rounded-md border border-line bg-surface px-2 py-0.5 text-muted">
              cosine <span className="font-mono text-ink">{formatScore(chunk.score)}</span>
            </span>
            {chunk.original_score !== null && chunk.original_score !== undefined ? (
              <span className="rounded-md border border-line bg-surface px-2 py-0.5 text-muted">
                pre-rerank <span className="font-mono text-ink">{formatScore(chunk.original_score)}</span>
              </span>
            ) : null}
            <span className="rounded-md border border-line bg-surface px-2 py-0.5 text-muted">
              chunk <span className="font-mono text-ink">#{chunk.chunk_index ?? "—"}</span>
            </span>
            <span className="rounded-md border border-line bg-surface px-2 py-0.5 text-muted">
              document <span className="font-mono text-ink">{chunk.document_id}</span>
            </span>
          </div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-surface p-3 font-mono text-2xs leading-relaxed text-ink">
            {chunk.content}
          </pre>
          {!survives ? (
            <p className="text-2xs leading-relaxed text-faint">
              This chunk was retrieved but did not fit inside <span className="font-mono">top_k</span>
              , so it never reached the model. Raising top_k would include it.
            </p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
