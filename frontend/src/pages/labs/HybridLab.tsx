import { useCallback, useMemo, useState } from "react";
import { ArrowRight, GitCompareArrows, Info, Loader2, Scale, Sparkles, Waypoints } from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type {
  CompareColumn,
  CompareRow,
  RetrievalMode,
  RetrieveCompareResponse,
} from "@/lib/types";
import { useFlipList, useReducedMotion } from "@/motion";
import { useWorkspaces } from "@/state/workspace";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, Stat } from "@/components/ui/Card";
import { Input } from "@/components/ui/Field";
import { EmptyState, Spinner } from "@/components/ui/Feedback";
import { Concept, LabShell } from "./LabShell";

/**
 * Hybrid Retrieval Laboratory.
 *
 * The Retrieval Lab next door answers "what did we find?". This one answers the
 * harder question: "what would we have MISSED?". It runs one question through
 * three retrievers - dense (meaning), BM25 (exact words), and their RRF fusion -
 * over the IDENTICAL chunk population, and lays the three orderings side by
 * side.
 *
 * The centrepiece is the comparison table, which is FLIP-animated: when you
 * switch which mode drives the sort, the SAME passages physically slide to
 * their new ranks. That motion is not decoration - it is the only way to show a
 * reader that a chunk BM25 found first is sitting eleventh in the vector
 * results. A static table states that fact; a table where the row visibly
 * travels down the list makes you believe it, and teaches the reason hybrid
 * retrieval exists in one gesture.
 *
 * NOTHING HERE IS SIMULATED. The three columns come from the real retriever on
 * the real workspace, and a mode that found nothing shows "no results" rather
 * than being padded to look comparable.
 */

const MODES: { id: RetrievalMode; title: string; scale: string; blurb: string }[] = [
  {
    id: "dense",
    title: "Dense",
    scale: "cosine similarity",
    blurb: "Ranks by meaning. Finds related wording, weak on exact strings.",
  },
  {
    id: "bm25",
    title: "BM25",
    scale: "BM25 term score",
    blurb: "Ranks by exact words, rare terms weighted higher. Strong on codes.",
  },
  {
    id: "hybrid",
    title: "Hybrid (RRF)",
    scale: "reciprocal-rank score",
    blurb: "Fuses both orderings by rank only, so the two scales never clash.",
  },
];

// Questions where the three methods genuinely disagree, so the lab demonstrates
// its point on first run rather than needing a hand-tuned query.
const EXAMPLES = [
  "RFC 1918 private address ranges",
  "UNIT III concept generation",
  "What is a hypervisor?",
  "error code ECONNREFUSED meaning",
];

export default function HybridLab() {
  const { activeId, loading: workspacesLoading } = useWorkspaces();
  const reduced = useReducedMotion();

  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [running, setRunning] = useState(false);
  const [data, setData] = useState<RetrieveCompareResponse | null>(null);
  const [error, setError] = useState("");
  // Which mode's order the comparison table is currently sorted by. Defaulting
  // to hybrid shows the FUSED result first, then the reader can ask "how did the
  // individual methods disagree?", which is the natural curiosity.
  const [sortBy, setSortBy] = useState<RetrievalMode>("hybrid");

  const run = useCallback(
    async (query: string) => {
      const trimmed = query.trim();
      if (!trimmed || !activeId) return;
      setRunning(true);
      setError("");
      try {
        setData(
          await api.chat.retrieveCompare({
            workspace_id: activeId,
            question: trimmed,
            top_k: 6,
            candidate_k: 12,
          }),
        );
      } catch (cause) {
        setError(cause instanceof ApiError ? cause.message : "The comparison failed.");
        setData(null);
      } finally {
        setRunning(false);
      }
    },
    [activeId],
  );

  if (workspacesLoading) {
    return (
      <LabShell title="Hybrid Retrieval" tagline="Loading..." concept={null}>
        <Card>
          <div className="px-5 py-10 text-center">
            <Spinner className="mx-auto" />
          </div>
        </Card>
      </LabShell>
    );
  }

  const rows = useMemo(() => {
    if (!data) return [] as CompareRow[];
    const sorted = [...data.comparison];
    sorted.sort((a, b) => {
      const ar = a.ranks[sortBy];
      const br = b.ranks[sortBy];
      // A chunk the sort-by mode did not retrieve sinks to the bottom, but is
      // still shown - because the fact that THIS mode missed it is the lesson.
      if (ar === null && br === null) return a.best_rank - b.best_rank;
      if (ar === null) return 1;
      if (br === null) return -1;
      return ar - br;
    });
    return sorted;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, sortBy]);

  return (
    <LabShell
      title="Hybrid Retrieval"
      tagline="One question through three retrievers - meaning, exact words, and their fusion - so you can see what each method finds alone."
      source="POST /api/chat/retrieve/compare → app/rag/retriever.py + app/rag/hybrid.py (no language model is called)"
      concept={
        <>
          <Concept label="What happens">
            Dense search ranks by MEANING; BM25 ranks by the EXACT words. Reciprocal Rank Fusion
            merges the two by each chunk's POSITION in each list, never by adding their
            (incomparable) scores.
          </Concept>
          <Concept label="Why it matters">
            Some answers live on an exact string - a unit number, an error code, a named standard.
            Meaning search blurs those apart. Seeing all three orderings of one query is what makes
            the trade-off visible instead of asserted.
          </Concept>
          <Concept label="Read the animation">
            Switch the sort between methods and watch the rows move. A passage that TRAVELS from
            the bottom to the top when you choose BM25 is the exact evidence meaning-only retrieval
            was about to miss.
          </Concept>
          <div className="rounded-lg border border-line bg-sunken px-3 py-2">
            <p className="font-medium text-ink">The honest note</p>
            <p className="mt-1">
              Each column reports its own score scale. A reciprocal-rank score is not a similarity,
              and the lab never shows them as if they were the same kind of number.
            </p>
          </div>
        </>
      }
      controls={
        <>
          <CardHeader
            title="Run the comparison"
            icon={<GitCompareArrows className="h-4 w-4" />}
            description="All three retrievers search the same chunks over the same scope."
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
              placeholder="Ask something..."
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
            <Button className="w-full" onClick={() => void run(question)} loading={running}>
              <Waypoints className="h-3.5 w-3.5" />
              Compare three retrievers
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

      {data ? (
        <>
          {/* headline counts, all real */}
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-3">
            <Stat
              label="Passages found"
              value={formatNumber(data.stats.total_unique_chunks)}
              hint="across all three methods"
              icon={<Scale className="h-3.5 w-3.5" />}
            />
            <Stat
              label="All three agree"
              value={formatNumber(data.stats.found_by_all_three)}
              hint="in every ranking"
              icon={<Sparkles className="h-3.5 w-3.5" />}
            />
            <Stat
              label="Contested"
              value={formatNumber(Math.max(0, data.stats.total_unique_chunks - data.stats.found_by_all_three))}
              hint="found by 1 or 2 methods"
              icon={<Info className="h-3.5 w-3.5" />}
            />
          </div>

          {/* the three columns */}
          <div className="grid gap-4 md:grid-cols-3">
            {MODES.map((mode) => (
              <ModeColumn
                key={mode.id}
                mode={mode}
                column={data.columns[mode.id]}
                active={sortBy === mode.id}
                onSort={() => setSortBy(mode.id)}
              />
            ))}
          </div>

          {/* the animated, unified comparison */}
          <ComparisonTable rows={rows} sortBy={sortBy} reduced={reduced} />

          {data.stats.top_disagreements.length > 0 ? (
            <div className="flex items-start gap-2 rounded-xl border border-caution/30 bg-caution/8 px-4 py-3">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-caution" />
              <div className="text-2xs leading-relaxed text-caution">
                <p className="font-medium">Where the methods disagreed most</p>
                <p className="mt-0.5 text-ink/80">
                  {data.stats.top_disagreements
                    .map((d) => `${d.label || d.document_name} (found only by ${d.found_by.join(", ")})`)
                    .join(" · ") || "No single method ranked a passage first that the others missed."}
                </p>
              </div>
            </div>
          ) : null}
        </>
      ) : !error && !running ? (
        <Card>
          <EmptyState
            icon={<GitCompareArrows className="h-5 w-5" />}
            title="Nothing compared yet"
            description="Ask a question above and the three retrievers will rank the same chunks, side by side."
          />
        </Card>
      ) : null}
    </LabShell>
  );
}

/* -------------------------------------------------------------------------- */

function ModeColumn({
  mode,
  column,
  active,
  onSort,
}: {
  mode: { id: RetrievalMode; title: string; scale: string; blurb: string };
  column: CompareColumn | undefined;
  active: boolean;
  onSort: () => void;
}) {
  const results = column?.results ?? [];
  return (
    <Card padded={false} className={cn("transition", active && "ring-1 ring-brand/50")}>
      <div className="border-b border-line px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs font-semibold text-ink">{mode.title}</p>
          <button
            type="button"
            onClick={onSort}
            className={cn(
              "rounded-md border px-2 py-0.5 text-2xs font-medium transition",
              active
                ? "border-brand/50 bg-brand/10 text-brand-ink"
                : "border-line text-muted hover:border-line-strong hover:text-ink",
            )}
          >
            {active ? "sorting by" : "sort by this"}
          </button>
        </div>
        <p className="mt-1 text-2xs text-faint">{mode.blurb}</p>
      </div>
      {column?.error ? (
        <div className="px-4 py-6 text-center text-2xs text-negative">{column.error}</div>
      ) : results.length === 0 ? (
        <div className="px-4 py-6 text-center text-2xs text-faint">No results for this question.</div>
      ) : (
        <ol className="divide-y divide-line">
          {results.map((result, index) => (
            <li key={`${result.vector_id}-${index}`} className="px-4 py-2.5">
              <div className="flex items-start gap-2">
                <span className="w-4 shrink-0 font-mono text-2xs text-faint">{index + 1}</span>
                <span className="min-w-0 flex-1 text-2xs leading-snug text-muted">
                  {result.label || result.document_name || "chunk"}
                </span>
                <span className="shrink-0 font-mono text-2xs text-ink">
                  {formatModeScore(mode.id, result)}
                </span>
              </div>
              <p className="mt-0.5 truncate pl-6 text-2xs text-faint">
                {result.document_name}
              </p>
            </li>
          ))}
        </ol>
      )}
      <div className="border-t border-line px-4 py-2 text-2xs text-faint">
        scale: <span className="font-mono">{column?.score_scale ?? mode.scale}</span>
        {column?.search_ms !== undefined ? (
          <>
            {" "}· <span className="font-mono">{column.search_ms} ms</span>
          </>
        ) : null}
      </div>
    </Card>
  );
}

function formatModeScore(mode: RetrievalMode, result: { score: number; rrf_score: number | null; bm25_score: number | null }): string {
  if (mode === "hybrid") {
    return result.rrf_score !== null ? result.rrf_score.toFixed(4) : result.score.toFixed(4);
  }
  if (mode === "bm25") {
    return (result.bm25_score ?? result.score).toFixed(2);
  }
  return result.score.toFixed(3);
}

/* -------------------------------------------------------------------------- */

/**
 * The unified comparison, FLIP-animated by rank position. Each row is keyed by
 * its vector id (stable across sorts) so the FLIP hook can track the SAME
 * passage as it moves when `sortBy` changes.
 */
function ComparisonTable({
  rows,
  sortBy,
  reduced,
}: {
  rows: CompareRow[];
  sortBy: RetrievalMode;
  reduced: boolean;
}) {
  // The FLIP signature changes whenever the sort order changes, which is exactly
  // when rows move. Keyed on sortBy + the ordered ids so re-running the same
  // question (identical order) does NOT re-animate.
  const signature = useMemo(
    () => sortBy + "|" + rows.map((r) => r.vector_id).join(","),
    [sortBy, rows],
  );
  const listRef = useFlipList<HTMLOListElement>(signature, { durationMs: reduced ? 0 : 520 });

  if (rows.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={<GitCompareArrows className="h-5 w-5" />}
          title="No passages retrieved"
          description="None of the three methods found anything for this question in this workspace."
        />
      </Card>
    );
  }

  return (
    <Card padded={false}>
      <CardHeader
        title="Where each method placed each passage"
        description="The same evidence, ranked three ways. Rows move when you change the sort — that travel is the disagreement."
        icon={<Scale className="h-4 w-4" />}
        actions={<Badge tone="accent">{sortBy === "hybrid" ? "fused order" : `${sortBy} order`}</Badge>}
      />
      <ol ref={listRef} className="divide-y divide-line">
        {rows.map((row, position) => (
          <li
            key={row.vector_id}
            data-flip-key={row.vector_id}
            className="flex items-center gap-3 px-5 py-3"
          >
            <span className="w-5 shrink-0 font-mono text-2xs text-faint">{position + 1}</span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs text-ink">
                {row.label || row.document_name || row.vector_id}
              </span>
              <span className="mt-0.5 block truncate text-2xs text-faint">{row.preview}</span>
            </span>
            <div className="flex shrink-0 items-center gap-1.5">
              {MODES.map((mode) => {
                const rank = row.ranks[mode.id];
                const isSort = mode.id === sortBy;
                return (
                  <span
                    key={mode.id}
                    title={`${mode.title} rank`}
                    className={cn(
                      "inline-flex h-6 w-6 items-center justify-center rounded-md font-mono text-2xs",
                      rank === null
                        ? "border border-dashed border-line text-faint"
                        : isSort
                          ? "bg-brand/15 font-semibold text-brand-ink"
                          : "bg-sunken text-muted",
                    )}
                  >
                    {rank === null ? "—" : rank + 1}
                  </span>
                );
              })}
            </div>
          </li>
        ))}
      </ol>
      <div className="flex items-center justify-between gap-3 border-t border-line px-5 py-2 text-2xs text-faint">
        <span className="flex items-center gap-1.5">
          {MODES.map((mode) => (
            <span key={mode.id}>{mode.title}</span>
          ))}
          <span className="ml-1 inline-flex items-center gap-1">
            <ArrowRight className="h-2.5 w-2.5" /> rank per method
          </span>
        </span>
        <span>A dash means that method did not retrieve this passage at all.</span>
      </div>
    </Card>
  );
}
