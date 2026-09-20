import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  Database,
  Eye,
  FileStack,
  Plus,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api } from "@/lib/api";
import {
  fileAccent,
  fileBadge,
  formatBytes,
  formatNumber,
  formatRelative,
  pluralize,
} from "@/lib/format";
import { useAsync } from "@/hooks/useAsync";
import { useWorkspaces } from "@/state/workspace";
import { useToast } from "@/state/toast";
import type { DocumentRecord } from "@/lib/types";
import { NoWorkspaceNotice, PageBody, PageHeader } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/Modal";
import { Input, Select } from "@/components/ui/Field";
import { EmptyState, ErrorState, ProgressBar, SkeletonRows } from "@/components/ui/Feedback";
import { UploadDialog } from "@/components/documents/UploadDialog";

/**
 * Knowledge Base.
 *
 * The inventory of what this workspace can answer from. Two things matter here
 * beyond listing files:
 *
 * 1. Ingestion state must be honest. A document that failed, or that was indexed
 *    with a different embedding model, is a document that will silently produce
 *    bad retrieval - so those states are surfaced prominently rather than being
 *    buried behind a "ready" filter.
 *
 * 2. Deletion is real and irreversible, so it goes through a confirmation that
 *    names the document and states what is removed.
 */
export default function KnowledgeBase() {
  const { activeId, active, loading: workspacesLoading } = useWorkspaces();
  const toast = useToast();

  const [uploadOpen, setUploadOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [pendingDelete, setPendingDelete] = useState<DocumentRecord | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [reindexing, setReindexing] = useState<number | null>(null);

  const documents = useAsync(
    activeId ? () => api.documents.list(activeId) : null,
    [activeId],
  );

  const stale = useAsync(
    activeId ? () => api.documents.stale(activeId) : null,
    [activeId],
  );

  const docs = documents.data?.documents ?? [];

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return docs.filter((doc) => {
      if (statusFilter !== "all" && doc.status !== statusFilter) return false;
      if (!needle) return true;
      return doc.original_filename.toLowerCase().includes(needle);
    });
  }, [docs, query, statusFilter]);

  const counts = useMemo(
    () => ({
      all: docs.length,
      ready: docs.filter((doc) => doc.status === "ready").length,
      processing: docs.filter((doc) => doc.status === "processing" || doc.status === "pending").length,
      failed: docs.filter((doc) => doc.status === "failed").length,
    }),
    [docs],
  );

  const handleDelete = useCallback(async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await api.documents.remove(pendingDelete.id);
      toast.success(
        `Deleted "${pendingDelete.original_filename}"`,
        `${pluralize(pendingDelete.chunk_count, "chunk")} and their vectors were removed.`,
      );
      setPendingDelete(null);
      documents.reload();
      stale.reload();
    } catch (cause) {
      toast.error(
        "Could not delete the document",
        cause instanceof ApiError ? cause.message : "Please try again.",
      );
    } finally {
      setDeleting(false);
    }
  }, [pendingDelete, toast, documents, stale]);

  const handleReindex = useCallback(
    async (doc: DocumentRecord) => {
      setReindexing(doc.id);
      try {
        await api.documents.reindex(doc.id);
        toast.success(
          `Re-indexing "${doc.original_filename}"`,
          "The text was re-read from the database and embedded again - no re-upload needed.",
        );
        // Give the background job a moment before refreshing the list.
        window.setTimeout(() => documents.reload(), 1500);
      } catch (cause) {
        toast.error(
          "Could not re-index",
          cause instanceof ApiError ? cause.message : "Please try again.",
        );
      } finally {
        setReindexing(null);
      }
    },
    [toast, documents],
  );

  if (!workspacesLoading && !activeId) {
    return (
      <>
        <PageHeader
          title="Knowledge Base"
          icon={<Database className="h-4.5 w-4.5" />}
          description="The documents this workspace can answer from."
        />
        <PageBody>
          <NoWorkspaceNotice />
        </PageBody>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Knowledge Base"
        description={
          active
            ? `Everything Carinaa can retrieve from in "${active.name}". Uploads are parsed, chunked and embedded automatically.`
            : undefined
        }
        icon={<Database className="h-4.5 w-4.5" />}
        badge={
          counts.all > 0 ? (
            <Badge tone="neutral" mono>
              {counts.all} files
            </Badge>
          ) : undefined
        }
        actions={
          <>
            <Button
              variant="secondary"
              size="sm"
              icon={<RefreshCw className="h-3.5 w-3.5" />}
              onClick={() => {
                documents.reload();
                stale.reload();
              }}
              disabled={documents.loading}
            >
              Refresh
            </Button>
            <Button
              variant="primary"
              size="sm"
              icon={<Plus className="h-3.5 w-3.5" />}
              onClick={() => setUploadOpen(true)}
            >
              Add document
            </Button>
          </>
        }
      />

      <PageBody wide className="space-y-5">
        {/* ---- stale-embedding warning ------------------------------- */}
        {stale.data && stale.data.total > 0 ? (
          <div className="rounded-xl border border-caution/30 bg-caution/8 p-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-caution" />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold text-caution">
                  {pluralize(stale.data.total, "document")} indexed with a different embedding model
                </p>
                <p className="mt-1 text-2xs leading-relaxed text-muted">
                  {stale.data.note ??
                    "These documents were embedded with a model that is no longer configured. Vectors from different models are not comparable, so retrieval quality would suffer. Re-indexing re-embeds them from the stored text."}
                </p>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {stale.data.documents.slice(0, 4).map((doc) => (
                    <Button
                      key={doc.id}
                      variant="secondary"
                      size="sm"
                      loading={reindexing === doc.id}
                      onClick={() => void handleReindex(doc)}
                    >
                      Re-index {doc.original_filename.slice(0, 28)}
                    </Button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {/* ---- filters ------------------------------------------------ */}
        {counts.all > 0 ? (
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter by filename…"
              icon={<Search className="h-3.5 w-3.5" />}
              wrapperClassName="flex-1"
            />
            <Select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              wrapperClassName="sm:w-44"
              options={[
                { value: "all", label: `All statuses (${counts.all})` },
                { value: "ready", label: `Ready (${counts.ready})` },
                { value: "processing", label: `Processing (${counts.processing})` },
                { value: "failed", label: `Failed (${counts.failed})` },
              ]}
            />
          </div>
        ) : null}

        {/* ---- list --------------------------------------------------- */}
        {documents.loading ? (
          <SkeletonRows rows={4} />
        ) : documents.error ? (
          <ErrorState message={documents.error} onRetry={documents.reload} />
        ) : counts.all === 0 ? (
          <EmptyState
            icon={<FileStack className="h-5 w-5" />}
            title="No documents in this workspace"
            description="Upload a file to make it searchable. PDF, DOCX, PPTX, XLSX, CSV, Markdown, TXT and JSON are supported, and each one keeps the provenance its citations need."
            action={
              <Button
                variant="primary"
                size="sm"
                icon={<Plus className="h-3.5 w-3.5" />}
                onClick={() => setUploadOpen(true)}
              >
                Add your first document
              </Button>
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={<Search className="h-5 w-5" />}
            title="No documents match those filters"
            description="Try a different filename or status."
            action={
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setQuery("");
                  setStatusFilter("all");
                }}
              >
                Clear filters
              </Button>
            }
          />
        ) : (
          <Card className="overflow-hidden">
            <ul className="divide-y divide-line">
              {filtered.map((doc) => (
                <DocumentRow
                  key={doc.id}
                  document={doc}
                  reindexing={reindexing === doc.id}
                  onReindex={() => void handleReindex(doc)}
                  onDelete={() => setPendingDelete(doc)}
                />
              ))}
            </ul>
          </Card>
        )}

        {/* ---- summary ------------------------------------------------- */}
        {counts.all > 0 ? (
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-line bg-surface px-4 py-3">
            <span className="text-2xs text-faint">
              {formatNumber(active?.stats?.chunks ?? 0)} chunks indexed
            </span>
            <span className="text-2xs text-faint">
              {formatNumber(active?.stats?.vectors ?? 0)} vectors in the store
            </span>
            <span className="text-2xs text-faint">
              {formatNumber(active?.stats?.characters ?? 0)} characters of text
            </span>
          </div>
        ) : null}
      </PageBody>

      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        workspaceId={activeId}
        onUploaded={() => documents.reload()}
      />

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        onClose={() => setPendingDelete(null)}
        onConfirm={() => void handleDelete()}
        busy={deleting}
        title="Delete this document?"
        confirmLabel="Delete permanently"
        message={
          pendingDelete ? (
            <div className="space-y-2.5">
              <p>
                <span className="font-medium text-ink">{pendingDelete.original_filename}</span> will
                be removed from this workspace.
              </p>
              <ul className="space-y-1 text-2xs">
                <li>
                  · Its {pluralize(pendingDelete.chunk_count, "chunk")} will be deleted from the
                  database.
                </li>
                <li>· Its vectors will be removed from the search index.</li>
                <li>· The stored file will be removed from disk.</li>
              </ul>
              <p className="text-2xs text-faint">
                Existing answers keep the citations they were given, but those sources will no
                longer be retrievable. This cannot be undone.
              </p>
            </div>
          ) : null
        }
      />
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Row                                                                         */
/* -------------------------------------------------------------------------- */

function DocumentRow({
  document: doc,
  reindexing,
  onReindex,
  onDelete,
}: {
  document: DocumentRecord;
  reindexing: boolean;
  onReindex: () => void;
  onDelete: () => void;
}) {
  const isProcessing = doc.status === "processing" || doc.status === "pending";

  return (
    <li className="group flex items-center gap-4 px-4 py-3.5 transition-colors hover:bg-sunken/50 sm:px-5">
      <span
        className={cn(
          "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border font-mono text-2xs font-semibold",
          fileAccent(doc.original_filename),
        )}
      >
        {fileBadge(doc.original_filename)}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={`/app/knowledge/${doc.id}`}
            className="truncate text-xs font-medium text-ink hover:text-brand hover:underline"
          >
            {doc.original_filename}
          </Link>
          <StatusPill status={doc.status} />
        </div>

        <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-2xs text-faint">
          <span>{formatBytes(doc.size_bytes)}</span>
          {doc.chunk_count > 0 ? (
            <>
              <span aria-hidden>·</span>
              <span>{pluralize(doc.chunk_count, "chunk")}</span>
            </>
          ) : null}
          {doc.page_count > 0 ? (
            <>
              <span aria-hidden>·</span>
              <span>{pluralize(doc.page_count, "page")}</span>
            </>
          ) : null}
          <span aria-hidden>·</span>
          <span>{formatRelative(doc.created_at)}</span>
          {doc.embedding_model ? (
            <>
              <span aria-hidden>·</span>
              <span className="font-mono">{doc.embedding_model.split("/").pop()}</span>
            </>
          ) : null}
        </div>

        {isProcessing ? (
          <div className="mt-2 max-w-xs">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-2xs text-brand">{doc.stage}</span>
              <span className="font-mono text-2xs text-faint">{doc.progress.toFixed(0)}%</span>
            </div>
            <ProgressBar value={doc.progress} height="sm" />
          </div>
        ) : null}

        {doc.status === "failed" && doc.error_message ? (
          <p className="mt-1.5 rounded border border-negative/25 bg-negative/8 px-2 py-1 text-2xs text-negative">
            {doc.error_message}
          </p>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <Link to={`/app/knowledge/${doc.id}`}>
          <Button variant="ghost" size="icon" aria-label="Inspect document" title="Inspect">
            <Eye className="h-3.5 w-3.5" />
          </Button>
        </Link>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Re-index document"
          title="Re-index from stored text"
          loading={reindexing}
          onClick={onReindex}
          disabled={isProcessing}
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Delete document"
          title="Delete"
          onClick={onDelete}
          className="hover:bg-negative/10 hover:text-negative"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
    </li>
  );
}

function StatusPill({ status }: { status: string }) {
  if (status === "ready") return <Badge tone="positive">ready</Badge>;
  if (status === "failed") return <Badge tone="negative">failed</Badge>;
  if (status === "processing") return <Badge tone="brand">processing</Badge>;
  return <Badge tone="neutral">pending</Badge>;
}
