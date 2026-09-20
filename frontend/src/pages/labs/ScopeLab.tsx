import { useCallback, useMemo, useState } from "react";
import { Check, Loader2, Play, Layers3 } from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api } from "@/lib/api";
import { formatScore } from "@/lib/format";
import type { RetrievedChunk, RetrievalOutcome } from "@/lib/types";
import { useAsync } from "@/hooks/useAsync";
import { useWorkspaces } from "@/state/workspace";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, Stat } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";
import { Concept, LabShell } from "./LabShell";

/**
 * Scope Lab: what changes when retrieval is limited to selected documents.
 *
 * This is the bench for the "answer only from PDF 3" use case. It runs the same
 * question across the whole workspace and then across only the documents you tick, so
 * the effect of scoping is visible rather than described.
 *
 * The filter is applied INSIDE the vector index - the out-of-scope vectors are never
 * even scored - so the right-hand column cannot contain a document you excluded. That
 * is the property worth demonstrating: scope is not a hint to the model, it is a
 * constraint on the search.
 */
export default function ScopeLab() {
  const { activeId, loading: workspacesLoading } = useWorkspaces();

  const documents = useAsync(
    activeId ? () => api.documents.list(activeId) : null,
    [activeId],
  );

  const [selected, setSelected] = useState<number[]>([]);
  const [question, setQuestion] = useState("What does the document say?");
  const [running, setRunning] = useState(false);
  const [whole, setWhole] = useState<RetrievalOutcome | null>(null);
  const [scoped, setScoped] = useState<RetrievalOutcome | null>(null);
  const [error, setError] = useState("");

  const ready = useMemo(
    () => (documents.data?.documents ?? []).filter((document) => document.status === "ready"),
    [documents.data],
  );

  const toggle = (id: number) =>
    setSelected((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );

  const run = useCallback(async () => {
    const trimmed = question.trim();
    if (!trimmed || !activeId || selected.length === 0) return;

    setRunning(true);
    setError("");
    try {
      const [everywhere, limited] = await Promise.all([
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
          document_ids: selected,
        }),
      ]);
      setWhole(everywhere.retrieval as RetrievalOutcome);
      setScoped(limited.retrieval as RetrievalOutcome);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Retrieval failed.");
      setWhole(null);
      setScoped(null);
    } finally {
      setRunning(false);
    }
  }, [activeId, question, selected]);

  if (workspacesLoading) {
    return (
      <LabShell title="Scope Lab" tagline="Loading…" concept={null}>
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
      title="Scope Lab"
      tagline="Limit retrieval to chosen documents and see exactly what changes."
      source="POST /api/chat/retrieve with and without document_ids"
      concept={
        <>
          <Concept label="What this shows">
            One question, searched twice: across the whole workspace, then across only
            the documents you tick.
          </Concept>
          <Concept label="Why it matters">
            This is how you ask &ldquo;answer only from this document&rdquo;. In Chat,
            the same thing happens through the <em>This chat</em> control, and the scope
            is enforced server-side.
          </Concept>
          <Concept label="Enforced, not requested">
            The filter runs inside the vector index, so excluded vectors are never scored.
            An out-of-scope document cannot appear in the right-hand column even in
            principle — it is not a prompt instruction the model could ignore.
          </Concept>
          <div className="rounded-lg border border-line bg-sunken px-3 py-2">
            <p className="font-medium text-ink">What to try</p>
            <p className="mt-1">
              Tick one document, run, then tick all of them and run again. When every
              document is in scope the two columns match — which shows the scope is the
              only difference.
            </p>
          </div>
        </>
      }
      controls={
        <>
          <CardHeader
            title="Choose the scope"
            description="Only indexed documents can be selected."
            icon={<Layers3 className="h-4 w-4" />}
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
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink outline-none transition focus:border-brand/50"
              />
            </div>

            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-2xs font-medium text-ink">
                  Documents in scope
                </span>
                <span className="font-mono text-2xs text-faint">
                  {selected.length}/{ready.length}
                </span>
              </div>

              {documents.loading ? (
                <p className="text-2xs text-faint">Loading documents…</p>
              ) : ready.length === 0 ? (
                <p className="text-2xs text-caution">
                  No indexed documents in this workspace yet.
                </p>
              ) : (
                <>
                  <div className="mb-2 flex gap-1.5">
                    <button
                      type="button"
                      onClick={() => setSelected(ready.map((document) => document.id))}
                      className="rounded-lg border border-line px-2 py-1 text-2xs text-muted transition hover:border-line-strong hover:text-ink"
                    >
                      Select all
                    </button>
                    <button
                      type="button"
                      onClick={() => setSelected([])}
                      className="rounded-lg border border-line px-2 py-1 text-2xs text-muted transition hover:border-line-strong hover:text-ink"
                    >
                      Clear
                    </button>
                  </div>

                  <ul className="max-h-52 space-y-0.5 overflow-y-auto scrollbar-thin">
                    {ready.map((document) => {
                      const checked = selected.includes(document.id);
                      return (
                        <li key={document.id}>
                          <button
                            type="button"
                            role="checkbox"
                            aria-checked={checked}
                            onClick={() => toggle(document.id)}
                            className={cn(
                              "flex w-full items-center gap-2 rounded-lg border px-2 py-1.5 text-left transition",
                              checked
                                ? "border-brand/35 bg-brand/8"
                                : "border-line hover:border-line-strong",
                            )}
                          >
                            <span
                              className={cn(
                                "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border",
                                checked
                                  ? "border-brand bg-brand text-brand-ink"
                                  : "border-line-strong bg-surface",
                              )}
                            >
                              {checked ? <Check className="h-2.5 w-2.5" /> : null}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-2xs text-ink">
                              {document.original_filename}
                            </span>
                            <span className="shrink-0 font-mono text-[0.625rem] text-faint">
                              {document.chunk_count} chunks
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </>
              )}
            </div>

            <Button
              className="w-full"
              onClick={() => void run()}
              loading={running}
              disabled={!activeId || selected.length === 0}
            >
              <Play className="h-3.5 w-3.5" />
              Compare scopes
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

      {whole && scoped ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <ScopeColumn
            title="Whole workspace"
            subtitle={`All ${ready.length} indexed document(s)`}
            outcome={whole}
            tone="neutral"
          />
          <ScopeColumn
            title="Selected scope"
            subtitle={`${selected.length} document(s) — everything else excluded`}
            outcome={scoped}
            tone="brand"
          />
        </div>
      ) : !error && !running ? (
        <Card>
          <EmptyState
            icon={<Layers3 className="h-5 w-5" />}
            title="Nothing compared yet"
            description="Tick at least one document, then run to see both scopes side by side."
          />
        </Card>
      ) : null}
    </LabShell>
  );
}

function ScopeColumn({
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
  const names = [...new Set(chunks.map((chunk) => chunk.document_name))];

  return (
    <Card padded={false}>
      <CardHeader
        title={title}
        description={subtitle}
        actions={<Badge tone={tone}>{names.length} document(s)</Badge>}
      />
      <div className="grid grid-cols-2 gap-px bg-line">
        <Stat label="Returned" value={String(outcome.returned ?? 0)} />
        <Stat label="Top score" value={formatScore(outcome.top_score ?? 0)} />
      </div>
      {chunks.length === 0 ? (
        <p className="px-5 py-6 text-center text-2xs leading-relaxed text-faint">
          Nothing matched in this scope.
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
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
