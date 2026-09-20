import { useMemo, useState } from "react";
import { Database, FileText, HardDrive, Layers, Loader2 } from "lucide-react";

import { cn } from "@/lib/cn";
import { api } from "@/lib/api";
import { formatBytes, formatNumber } from "@/lib/format";
import type { ChunkPreview, DocumentRecord } from "@/lib/types";
import { useAsync } from "@/hooks/useAsync";
import { useWorkspaces } from "@/state/workspace";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState, ErrorState, LoadingPanel, Spinner } from "@/components/ui/Feedback";
import { Concept, LabShell } from "./LabShell";

/**
 * Vector Store Laboratory.
 *
 * Shows what is actually IN the vector database, per document. This is the lab
 * that answers "did my document really get indexed?", which is otherwise invisible
 * until retrieval returns nothing and you have no idea why.
 *
 * It also makes the storage cost of chunking concrete: more chunks means more
 * vectors, and the embedding model and dimension are shown per document so a
 * mixed-model workspace is visible rather than silent.
 */

interface ChunkStats {
  count?: number;
  characters?: { total?: number; min?: number; max?: number; mean?: number };
  block_types?: Record<string, number>;
  pages?: number[];
  sections?: string[];
}

export default function VectorStoreLab() {
  const { activeId, active, loading: workspacesLoading } = useWorkspaces();
  const [openDocument, setOpenDocument] = useState<number | null>(null);

  const documents = useAsync(
    activeId ? () => api.documents.list(activeId) : null,
    [activeId],
  );

  const rows = documents.data?.documents ?? [];
  const ready = useMemo(() => rows.filter((d) => d.status === "ready"), [rows]);

  const totals = useMemo(() => {
    return {
      documents: ready.length,
      chunks: ready.reduce((sum, d) => sum + (d.chunk_count || 0), 0),
      vectors: ready.reduce((sum, d) => sum + (d.chunk_count || 0), 0),
      tokens: ready.reduce((sum, d) => sum + (d.token_estimate || 0), 0),
      bytes: ready.reduce((sum, d) => sum + (d.size_bytes || 0), 0),
    };
  }, [ready]);

  const models = useMemo(
    () => Array.from(new Set(ready.map((d) => d.embedding_model).filter(Boolean))),
    [ready],
  );
  const dimensions = useMemo(
    () => Array.from(new Set(ready.map((d) => d.embedding_dim).filter(Boolean))),
    [ready],
  );

  if (workspacesLoading) return <LoadingPanel message="Loading workspaces…" />;

  if (!activeId) {
    return (
      <LabShell
        title="Vector Store Laboratory"
        tagline="What is actually stored, and how much space it costs."
        concept={<Concept label="No workspace">Create a workspace and upload a document first.</Concept>}
      >
        <Card>
          <EmptyState
            icon={<Database className="h-5 w-5" />}
            title="No workspace selected"
            description="The vector store is scoped per workspace, so there is nothing to show yet."
          />
        </Card>
      </LabShell>
    );
  }

  return (
    <LabShell
      title="Vector Store Laboratory"
      tagline="What is actually in the vector database, per document. Nothing here is estimated — these are the stored rows."
      source="GET /api/workspaces/{id}/documents + GET /api/documents/{id}/chunks + /chunk-stats"
      concept={
        <>
          <Concept label="What happens">
            Every chunk produced by the ingestion pipeline is embedded and written to the vector
            store, alongside the metadata needed to trace it back to a page or a row.
          </Concept>
          <Concept label="Why it exists">
            The vector store is the ONE place the ingestion and query pipelines meet. Ingestion
            writes; querying reads. Neither knows about the other, which is what lets ingestion be
            slow-but-once and querying be fast-and-constant.
          </Concept>
          <Concept label="One vector per chunk">
            Storage cost is linear in chunks, not documents. Halving the chunk size roughly doubles
            the vector count.
          </Concept>
          <div className="rounded-lg border border-line bg-sunken px-3 py-2">
            <p className="font-medium text-ink">Vectors are not shown here</p>
            <p className="mt-1">
              The Embedding Lab shows the vectors. This lab shows what they are attached to — the
              text, the provenance, and the counts.
            </p>
          </div>
        </>
      }
    >
      {documents.error ? (
        <Card>
          <ErrorState
            title="Could not load documents"
            message={documents.error}
            onRetry={documents.reload}
          />
        </Card>
      ) : null}

      <Card padded={false}>
        <CardHeader
          title={active?.name ?? "Workspace"}
          description={`${formatNumber(totals.documents)} indexed ${totals.documents === 1 ? "document" : "documents"} · ${formatNumber(totals.vectors)} vectors · ${formatNumber(totals.tokens)} tokens estimated · ${formatBytes(totals.bytes)} of source files`}
          icon={<Database className="h-4 w-4" />}
          actions={
            models.length > 1 ? (
              <Badge tone="caution">mixed embedding models</Badge>
            ) : models.length === 1 ? (
              <Badge tone="positive">{dimensions[0]} dims</Badge>
            ) : null
          }
        />

        {documents.loading ? (
          <div className="px-5 py-8 text-center">
            <Spinner className="mx-auto" />
          </div>
        ) : ready.length === 0 ? (
          <EmptyState
            icon={<Layers className="h-5 w-5" />}
            title="No indexed documents"
            description="Upload a document in the Knowledge Base and its chunks will appear here."
          />
        ) : (
          <div className="divide-y divide-line">
            {ready.map((document) => (
              <DocumentRow
                key={document.id}
                document={document}
                open={openDocument === document.id}
                onToggle={() =>
                  setOpenDocument(openDocument === document.id ? null : document.id)
                }
              />
            ))}
          </div>
        )}
      </Card>

      {models.length > 1 ? (
        <Card>
          <div className="px-5 py-4 text-2xs leading-relaxed text-caution">
            <p className="font-medium">This workspace contains vectors from more than one model.</p>
            <p className="mt-1 text-muted">
              {models.join(", ")}. Similarity between vectors from different models is not
              meaningful — the numbers still look like scores, but they are not comparable. Re-index
              the older documents to bring them onto one model.
            </p>
          </div>
        </Card>
      ) : null}
    </LabShell>
  );
}

function DocumentRow({
  document,
  open,
  onToggle,
}: {
  document: DocumentRecord;
  open: boolean;
  onToggle: () => void;
}) {
  const chunks = useAsync(
    open ? () => api.documents.chunks(document.id, 60, 0) : null,
    [document.id, open],
  );
  const stats = useAsync<ChunkStats>(
    open ? () => api.documents.chunkStats(document.id) as Promise<ChunkStats> : null,
    [document.id, open],
  );

  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-5 py-3 text-left transition hover:bg-raised"
      >
        <FileText className="h-4 w-4 shrink-0 text-muted" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium text-ink">
            {document.original_filename}
          </span>
          <span className="mt-0.5 block text-2xs text-faint">
            {document.file_type} · {formatBytes(document.size_bytes)} ·{" "}
            {document.page_count ? `${document.page_count} pages · ` : ""}
            {document.embedding_model.split("/").pop()}
          </span>
        </span>
        <span className="shrink-0 font-mono text-2xs text-ink">
          {formatNumber(document.chunk_count)}
        </span>
        <span className="shrink-0 text-2xs text-faint">vectors</span>
        {open ? (
          <Loader2
            className={cn("h-3.5 w-3.5 shrink-0 text-faint", chunks.loading && "animate-spin")}
          />
        ) : null}
      </button>

      {open ? (
        <div className="animate-fade-up space-y-3 border-t border-line bg-sunken px-5 py-4">
          {stats.data ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <MiniStat label="Chunks" value={formatNumber(stats.data.count ?? 0)} />
              <MiniStat
                label="Characters"
                value={formatNumber(stats.data.characters?.total ?? 0)}
              />
              <MiniStat
                label="Mean chunk"
                value={`${formatNumber(stats.data.characters?.mean ?? 0)} ch`}
              />
              <MiniStat
                label="Range"
                value={`${formatNumber(stats.data.characters?.min ?? 0)}–${formatNumber(stats.data.characters?.max ?? 0)}`}
              />
            </div>
          ) : null}

          {stats.data?.block_types ? (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(stats.data.block_types).map(([type, count]) => (
                <span
                  key={type}
                  className="rounded-md border border-line bg-surface px-2 py-0.5 text-2xs text-muted"
                >
                  {type} <span className="font-mono text-ink">{count}</span>
                </span>
              ))}
            </div>
          ) : null}

          {chunks.loading ? (
            <div className="py-4 text-center">
              <Spinner className="mx-auto" />
            </div>
          ) : (
            <div className="space-y-1.5">
              <p className="text-2xs font-medium text-ink">
                First {Math.min(60, chunks.data?.length ?? 0)} chunks as stored
              </p>
              {(chunks.data ?? []).map((chunk) => (
                <StoredChunk key={chunk.id} chunk={chunk} />
              ))}
              {document.chunk_count > 60 ? (
                <p className="pt-1 text-2xs text-faint">
                  Showing 60 of {formatNumber(document.chunk_count)}. The Document Viewer has the
                  full list.
                </p>
              ) : null}
            </div>
          )}

          <div className="flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2">
            <HardDrive className="h-3.5 w-3.5 shrink-0 text-faint" />
            <p className="text-2xs leading-relaxed text-muted">
              Each row above is one vector in the store. The{" "}
              <span className="font-mono">[n]</span> a citation resolves to is created later, in the
              Context Lab — numbering is per question, not per chunk.
            </p>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function StoredChunk({ chunk }: { chunk: ChunkPreview }) {
  const section = chunk.doc_metadata?.section as string | undefined;
  const page = chunk.doc_metadata?.page_number as number | undefined;

  return (
    <details className="group rounded-lg border border-line bg-surface">
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-2xs">
        <span className="w-12 shrink-0 font-mono text-faint">#{chunk.chunk_index}</span>
        <span className="min-w-0 flex-1 truncate text-muted">
          {chunk.content.slice(0, 90).replace(/\s+/g, " ")}
        </span>
        <span className="shrink-0 font-mono text-faint">{formatNumber(chunk.token_estimate)} tok</span>
        {page ? <Badge tone="neutral">p.{page}</Badge> : null}
      </summary>
      <div className="border-t border-line px-3 py-2.5">
        {section ? (
          <p className="mb-1.5 text-2xs text-faint">
            section: <span className="text-muted">{section}</span> · range{" "}
            <span className="font-mono">
              {formatNumber(chunk.char_start)}–{formatNumber(chunk.char_end)}
            </span>{" "}
            · block <span className="font-mono">{chunk.block_type}</span>
          </p>
        ) : null}
        <pre className="max-h-56 overflow-auto whitespace-pre-wrap font-mono text-2xs leading-relaxed text-ink">
          {chunk.content}
        </pre>
      </div>
    </details>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-2.5 py-2">
      <p className="text-2xs text-faint">{label}</p>
      <p className="mt-0.5 font-mono text-xs text-ink">{value}</p>
    </div>
  );
}
