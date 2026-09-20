import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Boxes,
  Database,
  FileStack,
  FlaskConical,
  LayoutDashboard,
  MessagesSquare,
  Plus,
  Search,
  Sparkles,
  Waypoints,
  WifiOff,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { api } from "@/lib/api";
import {
  fileAccent,
  fileBadge,
  formatBytes,
  formatCompact,
  formatNumber,
  formatRelative,
  pluralize,
  truncate,
} from "@/lib/format";
import { useAsync } from "@/hooks/useAsync";
import { useWorkspaces } from "@/state/workspace";
import { useAuth } from "@/state/auth";
import { PageBody, PageHeader } from "@/components/layout/AppShell";
import { Badge, StatusDot } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, Stat } from "@/components/ui/Card";
import { EmptyState, ErrorState, LoadingPanel, SkeletonRows } from "@/components/ui/Feedback";
import { UploadDialog } from "@/components/documents/UploadDialog";

/**
 * Dashboard.
 *
 * The first screen after sign-in, and it has one job: tell the user what state
 * their knowledge base is in and what to do next. So it leads with readiness
 * (can I ask a question yet?) rather than with vanity metrics.
 */
export default function Dashboard() {
  const { user } = useAuth();
  const { active, activeId, loading: workspacesLoading, create } = useWorkspaces();
  const navigate = useNavigate();

  const [uploadOpen, setUploadOpen] = useState(false);

  const documents = useAsync(
    activeId ? () => api.documents.list(activeId) : null,
    [activeId],
  );

  const conversations = useAsync(
    activeId ? () => api.chat.conversations(activeId) : null,
    [activeId],
  );

  const overview = useAsync(() => api.analytics.overview(), []);

  const docs = documents.data?.documents ?? [];
  const readyCount = docs.filter((doc) => doc.status === "ready").length;
  const processingCount = docs.filter(
    (doc) => doc.status === "processing" || doc.status === "pending",
  ).length;
  const failedCount = docs.filter((doc) => doc.status === "failed").length;

  const stats = active?.stats;
  const recentDocs = useMemo(() => docs.slice(0, 5), [docs]);
  const recentConversations = useMemo(
    () => (conversations.data?.conversations ?? []).slice(0, 5),
    [conversations.data],
  );

  const handleQuickWorkspace = useCallback(async () => {
    try {
      await create("My first workspace", "Documents I want to ask questions about.");
    } catch {
      /* the switcher surfaces the error */
    }
  }, [create]);

  const firstName = (user?.display_name || user?.email || "").split(/[@\s]/)[0];

  /* ---- no workspace yet ---------------------------------------------- */
  if (!workspacesLoading && !activeId) {
    return (
      <>
        <PageHeader
          title="Dashboard"
          description="Create a workspace to begin. Everything in Carinaa lives inside one."
          icon={<LayoutDashboard className="h-4.5 w-4.5" />}
        />
        <PageBody>
          <EmptyState
            icon={<Database className="h-5 w-5" />}
            title="No workspace yet"
            description="A workspace is an isolated knowledge base. Documents you upload to one are never retrievable from another - that separation is enforced at the vector store, not just in the interface."
            action={
              <Button
                variant="primary"
                size="sm"
                icon={<Plus className="h-3.5 w-3.5" />}
                onClick={() => void handleQuickWorkspace()}
              >
                Create a workspace
              </Button>
            }
          />
        </PageBody>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={firstName ? `Welcome back, ${firstName}` : "Dashboard"}
        description={
          active
            ? `You are working in "${active.name}". Retrieval is scoped to this workspace only.`
            : "Loading your workspace…"
        }
        icon={<LayoutDashboard className="h-4.5 w-4.5" />}
        actions={
          <>
            <Button
              variant="secondary"
              size="sm"
              icon={<Search className="h-3.5 w-3.5" />}
              disabled={readyCount === 0}
              onClick={() => navigate("/app/chat")}
            >
              Ask a question
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

      <PageBody wide className="space-y-6">
        {/* ---- readiness ------------------------------------------------ */}
        {!documents.loading && docs.length === 0 ? (
          <div className="relative overflow-hidden rounded-xl border border-brand/25 bg-surface p-6 shadow-card">
            <div className="bg-brand-wash absolute inset-0" aria-hidden />
            <div className="relative flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-3.5">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand/12 text-brand">
                  <Sparkles className="h-5 w-5" />
                </span>
                <div>
                  <h2 className="text-sm font-semibold text-ink">
                    This workspace has no documents yet
                  </h2>
                  <p className="mt-1 max-w-xl text-xs leading-relaxed text-muted">
                    Upload a file and you can watch it move through parsing, normalisation,
                    chunking, embedding and indexing - each stage reporting real progress.
                    After that, asking a question will show you the full retrieval trace.
                  </p>
                </div>
              </div>
              <Button
                variant="primary"
                size="md"
                className="shrink-0"
                icon={<Plus className="h-4 w-4" />}
                onClick={() => setUploadOpen(true)}
              >
                Upload your first document
              </Button>
            </div>
          </div>
        ) : null}

        {/* ---- stats ---------------------------------------------------- */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Documents"
            value={formatNumber(stats?.documents ?? docs.length)}
            hint={
              readyCount > 0
                ? `${readyCount} ready${processingCount ? `, ${processingCount} processing` : ""}${
                    failedCount ? `, ${failedCount} failed` : ""
                  }`
                : "None indexed yet"
            }
            icon={<FileStack className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Chunks"
            value={formatCompact(stats?.chunks ?? 0)}
            hint="Retrievable passages"
            icon={<Boxes className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Conversations"
            value={formatNumber(stats?.conversations ?? 0)}
            hint={`${pluralize(stats?.messages ?? 0, "message")} total`}
            icon={<MessagesSquare className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Questions asked"
            value={formatNumber(stats?.queries ?? overview.data?.queries ?? 0)}
            hint="Recorded for analytics"
            icon={<BarChart3 className="h-3.5 w-3.5" />}
          />
        </div>

        {/* ---- main grid ------------------------------------------------ */}
        <div className="grid gap-6 lg:grid-cols-3">
          {/* recent documents */}
          <Card className="lg:col-span-2">
            <CardHeader
              title="Recent documents"
              description="The newest additions to this workspace."
              icon={<FileStack className="h-4 w-4" />}
              actions={
                <Link to="/app/knowledge">
                  <Button variant="ghost" size="sm" trailing={<ArrowRight className="h-3.5 w-3.5" />}>
                    View all
                  </Button>
                </Link>
              }
            />

            {documents.loading ? (
              <div className="p-5">
                <SkeletonRows rows={3} />
              </div>
            ) : documents.error ? (
              <div className="p-5">
                <ErrorState message={documents.error} onRetry={documents.reload} />
              </div>
            ) : recentDocs.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  icon={<FileStack className="h-4.5 w-4.5" />}
                  title="No documents yet"
                  description="Add a PDF, slide deck, spreadsheet or notes file to make this workspace searchable."
                  action={
                    <Button variant="primary" size="sm" onClick={() => setUploadOpen(true)}>
                      Add a document
                    </Button>
                  }
                />
              </div>
            ) : (
              <ul className="divide-y divide-line">
                {recentDocs.map((doc) => (
                  <li key={doc.id}>
                    <Link
                      to={`/app/knowledge/${doc.id}`}
                      className="flex items-center gap-3.5 px-5 py-3.5 transition-colors hover:bg-sunken/60"
                    >
                      <span
                        className={cn(
                          "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border font-mono text-2xs font-semibold",
                          fileAccent(doc.original_filename),
                        )}
                      >
                        {fileBadge(doc.original_filename)}
                      </span>

                      <div className="min-w-0 flex-1">
                        <p className="truncate text-xs font-medium text-ink">
                          {doc.original_filename}
                        </p>
                        <p className="mt-0.5 flex items-center gap-2 text-2xs text-faint">
                          <span>{formatBytes(doc.size_bytes)}</span>
                          {doc.chunk_count > 0 ? (
                            <>
                              <span aria-hidden>·</span>
                              <span>{pluralize(doc.chunk_count, "chunk")}</span>
                            </>
                          ) : null}
                          <span aria-hidden>·</span>
                          <span>{formatRelative(doc.created_at)}</span>
                        </p>
                      </div>

                      <DocumentStatusBadge status={doc.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {/* side column */}
          <div className="space-y-6">
            {/* conversations */}
            <Card>
              <CardHeader
                title="Recent conversations"
                icon={<MessagesSquare className="h-4 w-4" />}
              />
              {conversations.loading ? (
                <div className="p-5">
                  <SkeletonRows rows={2} />
                </div>
              ) : recentConversations.length === 0 ? (
                <div className="px-5 py-6 text-center">
                  <p className="text-2xs leading-relaxed text-muted">
                    No conversations yet. Ask your first question and it will appear here,
                    with its full trace.
                  </p>
                  <Button
                    variant="secondary"
                    size="sm"
                    className="mt-3"
                    disabled={readyCount === 0}
                    onClick={() => navigate("/app/chat")}
                  >
                    Start a conversation
                  </Button>
                </div>
              ) : (
                <ul className="divide-y divide-line">
                  {recentConversations.map((conversation) => (
                    <li key={conversation.id}>
                      <Link
                        to={`/app/chat/${conversation.id}`}
                        className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-sunken/60"
                      >
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-medium text-ink">
                            {conversation.title}
                          </p>
                          <p className="mt-0.5 text-2xs text-faint">
                            {pluralize(conversation.message_count, "message")} ·{" "}
                            {formatRelative(conversation.updated_at)}
                          </p>
                        </div>
                        <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-faint" />
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            {/* system readiness */}
            <Card>
              <CardHeader
                title="System readiness"
                description="Read live from the backend."
                icon={<Boxes className="h-4 w-4" />}
              />
              <div className="space-y-2.5 px-5 py-4">
                <ReadinessRow
                  label="Documents indexed"
                  ok={readyCount > 0}
                  detail={readyCount > 0 ? `${readyCount} ready` : "nothing indexed yet"}
                />
                <ReadinessRow
                  label="Embedding model"
                  ok={Boolean(stats && stats.chunks > 0)}
                  detail={
                    docs[0]?.embedding_model
                      ? `${docs[0].embedding_model.split("/").pop()} · ${docs[0].embedding_dim}-d`
                      : "loads on first use"
                  }
                />
                <ReadinessRow
                  label="Vector store"
                  ok={Boolean(stats && stats.vectors > 0)}
                  detail={
                    stats ? `${formatNumber(stats.vectors)} vectors stored` : "empty"
                  }
                />
                <div className="flex items-start gap-2.5 rounded-lg bg-sunken px-3 py-2.5">
                  <WifiOff className="mt-px h-3.5 w-3.5 shrink-0 text-accent" />
                  <p className="text-2xs leading-relaxed text-muted">
                    Offline mode uses a local model and makes no network calls at all. See{" "}
                    <Link
                      to="/app/settings"
                      className="font-medium text-brand underline decoration-brand/30 underline-offset-2"
                    >
                      Settings
                    </Link>{" "}
                    for the current provider status.
                  </p>
                </div>
              </div>
            </Card>

            {/* The laboratory is the educational core, so it gets a permanent
                entry point rather than only living in the sidebar. */}
            <Card>
              <CardHeader
                title="RAG Laboratory"
                description="Run one stage at a time and see what the system actually does."
                icon={<FlaskConical className="h-4 w-4" />}
              />
              <div className="space-y-2 p-5">
                <Link
                  to="/app/playground"
                  className="flex items-center justify-between gap-3 rounded-lg border border-line bg-surface px-3 py-2.5 transition hover:border-line-strong"
                >
                  <span className="min-w-0">
                    <span className="block text-xs font-medium text-ink">Open the laboratory</span>
                    <span className="mt-0.5 block text-2xs text-faint">
                      Seven benches: chunking, embeddings, retrieval, context, generation
                    </span>
                  </span>
                  <ArrowRight className="h-3.5 w-3.5 shrink-0 text-brand" />
                </Link>
                <Link
                  to="/app/chat?learning=1"
                  className="flex items-center justify-between gap-3 rounded-lg border border-line bg-surface px-3 py-2.5 transition hover:border-line-strong"
                >
                  <span className="min-w-0">
                    <span className="block text-xs font-medium text-ink">
                      Watch RAG think, in chat
                    </span>
                    <span className="mt-0.5 block text-2xs text-faint">
                      Learning Mode shows the real pipeline under every answer
                    </span>
                  </span>
                  <ArrowRight className="h-3.5 w-3.5 shrink-0 text-brand" />
                </Link>
              </div>
            </Card>
          </div>
        </div>
      </PageBody>

      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        workspaceId={activeId}
        onUploaded={() => {
          documents.reload();
        }}
      />
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Local pieces                                                                */
/* -------------------------------------------------------------------------- */

function DocumentStatusBadge({ status }: { status: string }) {
  if (status === "ready") return <Badge tone="positive">ready</Badge>;
  if (status === "failed") return <Badge tone="negative">failed</Badge>;
  if (status === "processing") return <Badge tone="brand">processing</Badge>;
  return <Badge tone="neutral">pending</Badge>;
}

function ReadinessRow({
  label,
  ok,
  detail,
}: {
  label: string;
  ok: boolean;
  detail: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="flex items-center gap-2 text-2xs text-muted">
        <StatusDot tone={ok ? "positive" : "neutral"} />
        {label}
      </span>
      <span className="truncate text-2xs font-medium text-ink">{detail}</span>
    </div>
  );
}

export { LoadingPanel, Waypoints, truncate };
