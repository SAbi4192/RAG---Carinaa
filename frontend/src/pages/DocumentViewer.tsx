import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  Boxes,
  ChevronLeft,
  ChevronRight,
  Cpu,
  Database,
  FileStack,
  Hash,
  Layers,
  RefreshCw,
  Ruler,
  Trash2,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api } from "@/lib/api";
import {
  fileAccent,
  fileBadge,
  formatBytes,
  formatDateTime,
  formatDuration,
  formatNumber,
  pluralize,
} from "@/lib/format";
import { useAsync } from "@/hooks/useAsync";
import { useToast } from "@/state/toast";
import { PageBody, PageHeader } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, Stat } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/Modal";
import { EmptyState, ErrorState, LoadingPanel, ProgressBar } from "@/components/ui/Feedback";

/**
 * Document viewer.
 *
 * Shows the document as the RETRIEVAL LAYER sees it, not as a prettified preview.
 * The chunk list is the point: it is what actually gets embedded, and it is what a
 * citation resolves to. Seeing the chunk boundaries - and the provenance attached
 * to each one - is how you understand why an answer cited page 4 and not page 5.
 *
 * This is also the honest view of a processing document: the real stage and the
 * real percentage, read from the database rather than animated locally.
 */

const PAGE_SIZE = 10;

export default function DocumentViewer() {
  const { documentId: rawId } = useParams();
  const documentId = rawId ? Number.parseInt(rawId, 10) : null;
  const navigate = useNavigate();
  const toast = useToast();

  /**
   * Citation deep-linking.
   *
   * `?chunk=<id>` asks the viewer to open ON the exact chunk a citation pointed
   * at, not merely at the top of the document. The chunk id is the primary key,
   * not the index, so the page number is computed by the backend
   * (`/chunks/<id>/position`) and we jump to that page, then highlight and scroll
   * to the entry once it renders.
   *
   * The "why this exists" matters more than the mechanics. A `[1]` that opens a
   * wall of 300 chunks does not prove the evidence - the reader has to hunt for
   * it, which is exactly what the citation was supposed to remove. Jumping to the
   * one passage and pulsing it is the difference between *claiming* traceability
   * and *demonstrating* it.
   */
  const [searchParams] = useSearchParams();
  const focusChunkId = (() => {
    const value = searchParams.get("chunk");
    const parsed = value ? Number.parseInt(value, 10) : NaN;
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  })();

  const [page, setPage] = useState(0);
  const [highlightedChunkId, setHighlightedChunkId] = useState<number | null>(null);
  const [reindexing, setReindexing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const document = useAsync(
    documentId ? () => api.documents.get(documentId) : null,
    [documentId],
  );

  const chunks = useAsync(
    documentId ? () => api.documents.chunks(documentId, PAGE_SIZE, page * PAGE_SIZE) : null,
    [documentId, page],
  );

  const stats = useAsync(
    documentId ? () => api.documents.chunkStats(documentId) : null,
    [documentId],
  );

  const doc = document.data;

  const isProcessing = doc?.status === "processing" || doc?.status === "pending";
  const totalPages = doc ? Math.max(1, Math.ceil(doc.chunk_count / PAGE_SIZE)) : 1;

  // Resolve the deep-linked chunk to its page (its id is a primary key, not an
  // index, so the backend answers the ordering question). Runs once per target.
  const positionResolvedFor = useRef<number | null>(null);
  useEffect(() => {
    if (!documentId || !focusChunkId) return;
    if (positionResolvedFor.current === focusChunkId) return;
    positionResolvedFor.current = focusChunkId;
    let cancelled = false;
    api.documents
      .chunkPosition(documentId, focusChunkId)
      .then((position) => {
        if (cancelled) return;
        setHighlightedChunkId(focusChunkId);
        setPage(Math.floor(position.ordinal / PAGE_SIZE));
      })
      .catch(() => {
        /* A missing/foreign chunk is simply not highlighted - the viewer still
           opens on the document, which is the pre-existing behaviour. */
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, focusChunkId]);

  // Once the target chunk is on screen (its page loaded), bring it into view
  // once. The user keeps the scroll wheel immediately after - this is a
  // one-shot reposition, not a hijack.
  useEffect(() => {
    if (highlightedChunkId == null) return;
    if (!chunks.data?.some((chunk) => chunk.id === highlightedChunkId)) return;
    const node = window.document.getElementById(`chunk-${highlightedChunkId}`);
    if (!node) return;
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    node.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one-shot per highlighted chunk, after its page renders
  }, [highlightedChunkId, chunks.data]);

  const handleReindex = useCallback(async () => {
    if (!documentId) return;
    setReindexing(true);
    try {
      await api.documents.reindex(documentId);
      toast.success(
        "Re-indexing started",
        "The stored text is being embedded again - no re-upload needed.",
      );
      window.setTimeout(() => {
        document.reload();
        chunks.reload();
        stats.reload();
      }, 1800);
    } catch (cause) {
      toast.error(
        "Could not re-index",
        cause instanceof ApiError ? cause.message : "Please try again.",
      );
    } finally {
      setReindexing(false);
    }
  }, [documentId, toast, document, chunks, stats]);

  const handleDelete = useCallback(async () => {
    if (!documentId) return;
    setDeleting(true);
    try {
      await api.documents.remove(documentId);
      toast.success("Document deleted", "Its chunks and vectors were removed.");
      navigate("/app/knowledge");
    } catch (cause) {
      toast.error(
        "Could not delete the document",
        cause instanceof ApiError ? cause.message : "Please try again.",
      );
      setDeleting(false);
      setConfirmDelete(false);
    }
  }, [documentId, toast, navigate]);

  /* ---- error / loading ------------------------------------------------ */
  if (document.loading) {
    return (
      <>
        <PageHeader title="Document" icon={<FileStack className="h-4.5 w-4.5" />} />
        <PageBody>
          <LoadingPanel message="Loading the document…" />
        </PageBody>
      </>
    );
  }

  if (document.error || !doc) {
    return (
      <>
        <PageHeader title="Document" icon={<FileStack className="h-4.5 w-4.5" />} />
        <PageBody>
          <ErrorState
            title="Document unavailable"
            message={
              document.error ||
              "This document does not exist, or it belongs to a workspace you do not have access to."
            }
            onRetry={document.reload}
          />
          <div className="mt-4">
            <Link to="/app/knowledge">
              <Button variant="secondary" size="sm" icon={<ArrowLeft className="h-3.5 w-3.5" />}>
                Back to the Knowledge Base
              </Button>
            </Link>
          </div>
        </PageBody>
      </>
    );
  }

  const metadata = doc.doc_metadata ?? {};
  const provenanceKeys = [
    "page_number",
    "page_end",
    "slide_number",
    "sheet_name",
    "row_start",
    "row_end",
    "section",
    "json_path",
  ].filter((key) => metadata[key] !== undefined && metadata[key] !== null);

  return (
    <>
      <PageHeader
        title={doc.original_filename}
        description={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span>{doc.file_type.toUpperCase()}</span>
            <span aria-hidden>·</span>
            <span>{formatBytes(doc.size_bytes)}</span>
            <span aria-hidden>·</span>
            <span>added {formatDateTime(doc.created_at)}</span>
          </span>
        }
        icon={<FileStack className="h-4.5 w-4.5" />}
        badge={
          <>
            <span
              className={cn(
                "flex h-6 w-9 items-center justify-center rounded border font-mono text-2xs font-semibold",
                fileAccent(doc.original_filename),
              )}
            >
              {fileBadge(doc.original_filename)}
            </span>
            <StatusBadge status={doc.status} />
          </>
        }
        actions={
          <>
            <Link to="/app/knowledge">
              <Button variant="ghost" size="sm" icon={<ArrowLeft className="h-3.5 w-3.5" />}>
                Back
              </Button>
            </Link>
            <Button
              variant="secondary"
              size="sm"
              icon={<RefreshCw className="h-3.5 w-3.5" />}
              loading={reindexing}
              disabled={isProcessing}
              onClick={() => void handleReindex()}
            >
              Re-index
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon={<Trash2 className="h-3.5 w-3.5" />}
              onClick={() => setConfirmDelete(true)}
              className="hover:bg-negative/10 hover:text-negative"
            >
              Delete
            </Button>
          </>
        }
      />

      <PageBody wide className="space-y-6">
        {/* ---- failure ------------------------------------------------- */}
        {doc.status === "failed" ? (
          <div className="rounded-xl border border-negative/30 bg-negative/8 p-4">
            <p className="flex items-center gap-2 text-xs font-semibold text-negative">
              <AlertTriangle className="h-4 w-4" />
              Ingestion failed
            </p>
            <p className="mt-1.5 text-2xs leading-relaxed text-muted">
              {doc.error_message || "The parser could not process this file."}
            </p>
            <p className="mt-2 text-2xs text-faint">
              Any partial chunks and vectors were removed, so this document is not being
              retrieved. Nothing is silently half-indexed.
            </p>
          </div>
        ) : null}

        {/* ---- processing ---------------------------------------------- */}
        {isProcessing ? (
          <Card className="p-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold text-ink">Processing</p>
                <p className="mt-0.5 text-2xs text-muted">
                  Current stage: <span className="font-medium text-brand">{doc.stage}</span>
                </p>
              </div>
              <span className="font-mono text-sm tabular-nums text-ink">
                {doc.progress.toFixed(0)}%
              </span>
            </div>
            <div className="mt-3">
              <ProgressBar value={doc.progress} />
            </div>
            <p className="mt-3 text-2xs text-faint">
              This percentage comes from the ingestion pipeline's own progress reporting, not
              from a client-side timer.
            </p>
          </Card>
        ) : null}

        {/* ---- stats --------------------------------------------------- */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Chunks"
            value={formatNumber(doc.chunk_count)}
            hint="Retrievable passages"
            icon={<Boxes className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Characters"
            value={formatNumber(doc.char_count)}
            hint={`~${formatNumber(doc.token_estimate)} tokens`}
            icon={<Ruler className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Pages / slides"
            value={doc.page_count > 0 ? formatNumber(doc.page_count) : "—"}
            hint={doc.page_count > 0 ? "Counted during parsing" : "Not applicable"}
            icon={<Layers className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Embedding"
            value={`${doc.embedding_dim}-d`}
            hint={doc.embedding_model.split("/").pop() || "—"}
            icon={<Cpu className="h-3.5 w-3.5" />}
          />
        </div>

        <div className="grid gap-6 lg:grid-cols-[20rem_1fr]">
          {/* ---- metadata -------------------------------------------- */}
          <div className="space-y-6">
            <Card>
              <CardHeader title="Details" icon={<Hash className="h-4 w-4" />} />
              <dl className="divide-y divide-line">
                <MetaRow label="Document ID" value={String(doc.id)} mono />
                <MetaRow label="Workspace" value={String(doc.workspace_id)} mono />
                <MetaRow label="File type" value={doc.file_type.toUpperCase()} />
                <MetaRow label="Size" value={formatBytes(doc.size_bytes)} />
                <MetaRow label="Status" value={doc.status} />
                <MetaRow label="Stage" value={doc.stage} />
                <MetaRow label="Created" value={formatDateTime(doc.created_at)} />
                <MetaRow
                  label="Processed"
                  value={doc.processed_at ? formatDateTime(doc.processed_at) : "—"}
                />
                <MetaRow label="Embedding model" value={doc.embedding_model} mono />
              </dl>
            </Card>

            {provenanceKeys.length > 0 ? (
              <Card>
                <CardHeader
                  title="Provenance available"
                  description="Keys this document's chunks carry, which is what makes its citations precise."
                  icon={<Database className="h-4 w-4" />}
                />
                <div className="flex flex-wrap gap-1.5 px-5 py-4">
                  {provenanceKeys.map((key) => (
                    <Badge key={key} tone="brand" mono>
                      {key}
                    </Badge>
                  ))}
                </div>
              </Card>
            ) : null}

            {Object.keys(doc.stage_timings ?? {}).length > 0 ? (
              <Card>
                <CardHeader
                  title="Stage timings"
                  description="Measured during ingestion."
                  icon={<RefreshCw className="h-4 w-4" />}
                />
                <dl className="divide-y divide-line">
                  {Object.entries(doc.stage_timings).map(([stage, value]) => (
                    <MetaRow
                      key={stage}
                      label={stage}
                      value={
                        typeof value === "number"
                          ? formatDuration(value)
                          : String(value ?? "—")
                      }
                      mono
                    />
                  ))}
                </dl>
              </Card>
            ) : null}

            {stats.data ? (
              <Card>
                <CardHeader
                  title="Chunk statistics"
                  description="Computed from the stored chunks."
                  icon={<Boxes className="h-4 w-4" />}
                />
                <div className="px-5 py-4">
                  <pre className="max-h-64 overflow-auto scrollbar-thin rounded-lg border border-line bg-sunken p-3 font-mono text-2xs leading-relaxed text-muted">
                    {JSON.stringify(stats.data, null, 2)}
                  </pre>
                </div>
              </Card>
            ) : null}
          </div>

          {/* ---- chunks ---------------------------------------------- */}
          <Card className="min-w-0">
            <CardHeader
              title="Chunks"
              description="Exactly what is embedded and retrievable. Each chunk keeps the location it came from."
              icon={<Boxes className="h-4 w-4" />}
              actions={
                <Badge tone="neutral" mono>
                  {formatNumber(doc.chunk_count)} total
                </Badge>
              }
            />

            {chunks.loading ? (
              <LoadingPanel message="Loading chunks…" />
            ) : chunks.error ? (
              <div className="p-5">
                <ErrorState message={chunks.error} onRetry={chunks.reload} />
              </div>
            ) : !chunks.data?.length ? (
              <div className="p-5">
                <EmptyState
                  icon={<Boxes className="h-4.5 w-4.5" />}
                  title="No chunks"
                  description="This document has not produced any chunks yet. If it failed, the reason is shown above."
                />
              </div>
            ) : (
              <>
                <ul className="divide-y divide-line">
                  {chunks.data.map((chunk) => {
                    const chunkMeta = chunk.doc_metadata ?? {};
                    const location = buildLocation(chunkMeta);

                    return (
                      <li
                        key={chunk.id}
                        id={`chunk-${chunk.id}`}
                        className={cn(
                          "px-5 py-4 transition-colors",
                          chunk.id === highlightedChunkId && "chunk-focus",
                        )}
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          {chunk.id === highlightedChunkId ? (
                            <Badge tone="positive">this passage</Badge>
                          ) : null}
                          <Badge tone="brand" mono>
                            #{chunk.chunk_index}
                          </Badge>
                          {location ? (
                            <Badge tone="accent" mono>
                              {location}
                            </Badge>
                          ) : null}
                          <Badge tone="neutral" mono>
                            {formatNumber(chunk.token_estimate)} tok
                          </Badge>
                          <Badge tone="neutral" mono>
                            {chunk.block_type}
                          </Badge>
                          <span className="font-mono text-2xs text-faint">
                            chars {formatNumber(chunk.char_start)}–{formatNumber(chunk.char_end)}
                          </span>
                        </div>

                        <p className="mt-2.5 whitespace-pre-wrap text-xs leading-relaxed text-muted">
                          {chunk.content}
                        </p>

                        {Object.keys(chunkMeta).length > 0 ? (
                          <details className="mt-2.5">
                            <summary className="cursor-pointer text-2xs text-faint transition-colors hover:text-muted">
                              Provenance metadata
                            </summary>
                            <pre className="mt-2 overflow-x-auto scrollbar-thin rounded border border-line bg-sunken p-2.5 font-mono text-2xs text-muted">
                              {JSON.stringify(chunkMeta, null, 2)}
                            </pre>
                          </details>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>

                {totalPages > 1 ? (
                  <div className="flex items-center justify-between gap-3 border-t border-line px-5 py-3">
                    <Button
                      variant="secondary"
                      size="sm"
                      icon={<ChevronLeft className="h-3.5 w-3.5" />}
                      disabled={page === 0}
                      onClick={() => setPage((value) => Math.max(0, value - 1))}
                    >
                      Previous
                    </Button>
                    <span className="font-mono text-2xs text-muted">
                      page {page + 1} of {totalPages}
                    </span>
                    <Button
                      variant="secondary"
                      size="sm"
                      trailing={<ChevronRight className="h-3.5 w-3.5" />}
                      disabled={page + 1 >= totalPages}
                      onClick={() => setPage((value) => value + 1)}
                    >
                      Next
                    </Button>
                  </div>
                ) : null}
              </>
            )}
          </Card>
        </div>
      </PageBody>

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => void handleDelete()}
        busy={deleting}
        title="Delete this document?"
        confirmLabel="Delete permanently"
        message={
          <p>
            <span className="font-medium text-ink">{doc.original_filename}</span> and its{" "}
            {pluralize(doc.chunk_count, "chunk")} will be removed, along with its vectors. This
            cannot be undone.
          </p>
        }
      />
    </>
  );
}

/* -------------------------------------------------------------------------- */

function buildLocation(metadata: Record<string, unknown>): string {
  const parts: string[] = [];
  if (metadata.page_number) {
    parts.push(
      metadata.page_end && metadata.page_end !== metadata.page_number
        ? `p.${metadata.page_number}-${metadata.page_end}`
        : `p.${metadata.page_number}`,
    );
  }
  if (metadata.slide_number) parts.push(`slide ${metadata.slide_number}`);
  if (metadata.sheet_name) {
    parts.push(
      metadata.row_start
        ? `${metadata.sheet_name} r${metadata.row_start}${metadata.row_end ? `-${metadata.row_end}` : ""}`
        : String(metadata.sheet_name),
    );
  }
  if (metadata.json_path) parts.push(String(metadata.json_path));
  return parts.join(" · ");
}

function MetaRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3 px-5 py-2.5">
      <dt className="shrink-0 text-2xs text-faint">{label.replace(/_/g, " ")}</dt>
      <dd
        className={cn(
          "min-w-0 break-words text-right text-2xs text-ink",
          mono && "font-mono",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === "ready") return <Badge tone="positive">ready</Badge>;
  if (status === "failed") return <Badge tone="negative">failed</Badge>;
  if (status === "processing") return <Badge tone="brand">processing</Badge>;
  return <Badge tone="neutral">pending</Badge>;
}

export { useMemo };
