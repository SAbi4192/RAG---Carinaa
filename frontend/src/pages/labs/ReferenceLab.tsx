import { useCallback, useMemo, useState } from "react";
import { FileSearch, Layers, Loader2, Play } from "lucide-react";

import { ApiError, api } from "@/lib/api";
import { formatScore } from "@/lib/format";
import type { RetrievedChunk, RetrievalOutcome } from "@/lib/types";
import { useWorkspaces } from "@/state/workspace";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, Stat } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";
import { Concept, LabShell } from "./LabShell";

/**
 * Reference Lab: how a page or section reference changes the search.
 *
 * THE POINT OF THIS BENCH
 * -----------------------
 * Run the same question twice - once as plain text, once as a structural reference -
 * and watch the results change. That comparison is the lesson, and it is one nobody
 * believes until they see it: the words "page 2" carry no meaning, so dense retrieval
 * cannot find page 2 by reading them. The filter does the work.
 *
 * The two runs are labelled "semantic only" and "with the filter applied" rather than
 * "wrong" and "right", because the first run is not broken - it is doing exactly what
 * meaning-based search should do with a question that has no meaning to match.
 */
export default function ReferenceLab() {
  const { activeId, active, loading: workspacesLoading } = useWorkspaces();

  const [question, setQuestion] = useState("Tell me about page 2");
  const [pageNumber, setPageNumber] = useState("");
  const [section, setSection] = useState("");

  const [running, setRunning] = useState(false);
  const [baseline, setBaseline] = useState<RetrievalOutcome | null>(null);
  const [filtered, setFiltered] = useState<RetrievalOutcome | null>(null);
  const [error, setError] = useState("");

  const run = useCallback(async () => {
    const trimmed = question.trim();
    if (!trimmed || !activeId) return;

    setRunning(true);
    setError("");
    try {
      const page = pageNumber.trim() ? Number(pageNumber) : undefined;
      const sectionName = section.trim() || undefined;

      // Two requests on purpose: the difference between them IS the demonstration.
      const [plain, constrained] = await Promise.all([
        api.chat.retrieve({
          workspace_id: activeId,
          question: trimmed,
          top_k: 5,
          candidate_k: 30,
        }),
        api.chat.retrieve({
          workspace_id: activeId,
          question: trimmed,
          top_k: 5,
          candidate_k: 30,
          page_number: page,
          section: sectionName,
        }),
      ]);

      setBaseline(plain.retrieval as RetrievalOutcome);
      setFiltered(constrained.retrieval as RetrievalOutcome);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Retrieval failed.");
      setBaseline(null);
      setFiltered(null);
    } finally {
      setRunning(false);
    }
  }, [activeId, question, pageNumber, section]);

  const detected = useMemo(() => {
    // Mirrors the backend's detector so the bench can show what WILL be detected
    // before the run. The filter actually applied is always the server's.
    const match = question.match(/\b(?:pages?|p\.?)\s*(?:no\.?|number)?\s*(\d{1,4})\b/i);
    if (match) return `page ${match[1]} (from the wording)`;
    const ordinals: Record<string, number> = {
      first: 1, second: 2, third: 3, fourth: 4, fifth: 5,
      sixth: 6, seventh: 7, eighth: 8, ninth: 9, tenth: 10,
    };
    const word = question.match(/\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+page\b/i);
    if (word) return `page ${ordinals[word[1].toLowerCase()]} (from "${word[0]}")`;
    if (/\b(previous|next|last|following)\s+page\b/i.test(question)) {
      return "a relative page reference — resolved from the conversation";
    }
    return null;
  }, [question]);

  if (workspacesLoading) {
    return (
      <LabShell title="Reference Lab" tagline="Loading…" concept={null}>
        <Card>
          <div className="px-5 py-10 text-center">
            <Loader2 className="mx-auto h-4 w-4 animate-spin text-faint" />
          </div>
        </Card>
      </LabShell>
    );
  }

  return (
    <LabShell
      title="Reference Lab"
      tagline="See how a page or section reference changes what retrieval finds."
      source="POST /api/chat/retrieve with and without page_number / section"
      concept={
        <>
          <Concept label="What this shows">
            The same question is searched twice — once as plain meaning, once with a
            structural filter. The two result sets are shown side by side.
          </Concept>
          <Concept label="Why the filter is needed">
            Dense retrieval compares <em>meaning</em>. The words &ldquo;page 2&rdquo; have
            no semantic relationship to whatever is written on page 2, so the search
            cannot find it by reading the question. The constraint has to be applied as a
            filter inside the index.
          </Concept>
          <div className="rounded-lg border border-line bg-sunken px-3 py-2">
            <p className="font-medium text-ink">What to try</p>
            <p className="mt-1">
              Run it, then clear the page box and run again. Same question, different
              evidence — that difference is the whole lesson.
            </p>
          </div>
        </>
      }
      controls={
        <>
          <CardHeader
            title="Ask a question with a reference"
            description="Both searches run against the real index; no language model is called."
            icon={<FileSearch className="h-4 w-4" />}
            actions={running ? <Loader2 className="h-3.5 w-3.5 animate-spin text-faint" /> : null}
          />
          <div className="space-y-3.5 p-5">
            <div>
              <label className="mb-1.5 block text-2xs font-medium text-ink">Question</label>
              <input
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void run();
                }}
                placeholder="Tell me about page 2"
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink outline-none transition focus:border-brand/50"
              />
              {detected ? (
                <p className="mt-1.5 text-2xs text-brand">
                  Detected: {detected}
                </p>
              ) : (
                <p className="mt-1.5 text-2xs text-faint">
                  No page reference in this wording.
                </p>
              )}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="mb-1.5 block text-2xs font-medium text-ink">
                  Page filter
                </label>
                <input
                  value={pageNumber}
                  onChange={(event) => setPageNumber(event.target.value.replace(/\D/g, ""))}
                  placeholder="auto-detected"
                  className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink outline-none transition focus:border-brand/50"
                />
                <p className="mt-1 text-2xs text-faint">
                  Leave empty to use what the question says.
                </p>
              </div>
              <div>
                <label className="mb-1.5 block text-2xs font-medium text-ink">
                  Section filter
                </label>
                <input
                  value={section}
                  onChange={(event) => setSection(event.target.value)}
                  placeholder="e.g. Interviewing Techniques"
                  className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink outline-none transition focus:border-brand/50"
                />
                <p className="mt-1 text-2xs text-faint">Exact stored section title.</p>
              </div>
            </div>

            <Button className="w-full" onClick={() => void run()} loading={running} disabled={!activeId}>
              <Play className="h-3.5 w-3.5" />
              Compare both searches
            </Button>

            {!active?.stats?.chunks ? (
              <p className="text-2xs text-caution">
                This workspace has no indexed chunks yet, so both searches will be empty.
              </p>
            ) : null}
          </div>
        </>
      }
    >
      {error ? (
        <Card>
          <div className="px-5 py-4 text-xs text-negative">{error}</div>
        </Card>
      ) : null}

      {baseline && filtered ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <ResultColumn
            title="Semantic only"
            subtitle="What meaning-based search finds on its own"
            outcome={baseline}
            tone="neutral"
          />
          <ResultColumn
            title="With the filter applied"
            subtitle="The same question, constrained to the reference"
            outcome={filtered}
            tone="brand"
          />
        </div>
      ) : !error && !running ? (
        <Card>
          <EmptyState
            icon={<Layers className="h-5 w-5" />}
            title="Nothing compared yet"
            description="Ask a question with a page or section reference, and both searches will run."
          />
        </Card>
      ) : null}
    </LabShell>
  );
}

function ResultColumn({
  title,
  subtitle,
  outcome,
  tone,
}: {
  title: string;
  subtitle: string;
  outcome: RetrievalOutcome;
  tone: "neutral" | "brand";
}) {
  const chunks: RetrievedChunk[] = outcome.chunks ?? [];
  return (
    <Card padded={false}>
      <CardHeader
        title={title}
        description={subtitle}
        actions={<Badge tone={tone}>{chunks.length} chunks</Badge>}
      />
      <div className="grid grid-cols-2 gap-px bg-line">
        <Stat label="Returned" value={String(outcome.returned ?? 0)} />
        <Stat label="Top score" value={formatScore(outcome.top_score ?? 0)} />
      </div>
      {chunks.length === 0 ? (
        <p className="px-5 py-6 text-center text-2xs leading-relaxed text-faint">
          Nothing matched. With a filter applied this is a real result, not a failure —
          it means no indexed chunk covers that reference.
        </p>
      ) : (
        <ol className="divide-y divide-line">
          {chunks.map((chunk, index) => (
            <li key={`${chunk.vector_id}-${index}`} className="px-4 py-2.5">
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate text-2xs font-medium text-ink">
                  {chunk.document_name}
                </span>
                <span className="shrink-0 font-mono text-2xs text-faint">
                  {formatScore(chunk.score)}
                </span>
              </div>
              <p className="mt-1 line-clamp-2 text-2xs leading-relaxed text-muted">
                {chunk.content.slice(0, 160).replace(/\s+/g, " ")}
              </p>
              <p className="mt-1 font-mono text-[0.625rem] text-faint">
                {chunk.metadata?.page_number
                  ? `p.${chunk.metadata.page_number}${
                      chunk.metadata.page_end && chunk.metadata.page_end !== chunk.metadata.page_number
                        ? `–${chunk.metadata.page_end}`
                        : ""
                    }`
                  : "no page metadata"}
                {chunk.metadata?.section ? ` · ${String(chunk.metadata.section).slice(0, 40)}` : ""}
              </p>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
