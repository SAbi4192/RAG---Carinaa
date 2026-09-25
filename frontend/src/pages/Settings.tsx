import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  BadgeCheck,
  CheckCircle2,
  Cpu,
  Database,
  Download,
  FolderCog,
  Info,
  Languages,
  Monitor,
  Moon,
  Palette,
  Pencil,
  RefreshCw,
  Server,
  Settings as SettingsIcon,
  Shield,
  ShieldAlert,
  Sun,
  Trash2,
  WifiOff,
  XCircle,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api } from "@/lib/api";
import { formatNumber, formatScore } from "@/lib/format";
import { useAsync } from "@/hooks/useAsync";
import { useTheme } from "@/state/theme";
import { useToast } from "@/state/toast";
import { useWorkspaces } from "@/state/workspace";
import { PageBody, PageHeader } from "@/components/layout/AppShell";
import { Badge, StatusDot } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Field";
import { ConfirmDialog } from "@/components/ui/Modal";
import { Segmented, Tabs, TabPanel } from "@/components/ui/Tabs";
import { EmptyState, ErrorState, LoadingPanel, Spinner } from "@/components/ui/Feedback";

/**
 * Settings.
 *
 * Two rules govern this page:
 *
 * 1. NO SECRETS. Not the API keys, not their prefixes, not whether they "look
 *    valid". The server exposes only whether a provider is configured and whether
 *    it is currently available. There is no endpoint that returns a key, so there
 *    is nothing for this page to leak.
 *
 * 2. CONFIGURATION IS READ-ONLY FROM THE BROWSER. Retrieval parameters come from
 *    server-side settings. Letting the client change `top_k` for a whole workspace
 *    would be a foot-gun; the Playground lets you experiment per-query instead,
 *    which is where experimentation belongs.
 *
 * The Security tab runs the real self-tests. When one cannot run it reports
 * "skipped" with the reason rather than passing by default - a security check that
 * silently succeeds is worse than no check.
 */

type SettingsTab = "providers" | "retrieval" | "security" | "system" | "workspace";

export default function Settings() {
  const { theme, setTheme } = useTheme();
  const toast = useToast();
  const navigate = useNavigate();
  const { active, refresh: refreshWorkspaces, remove: removeWorkspace } = useWorkspaces();

  const [tab, setTab] = useState<SettingsTab>("providers");

  /* ---- workspace management (Danger Zone) --------------------------------
     Deleting a workspace destroys its documents, chunks, vectors and
     conversations. That is irreversible, so the confirmation names exactly what
     is lost rather than asking a generic "are you sure?" - a user cannot consent
     to a consequence they have not been told. */
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deletingWorkspace, setDeletingWorkspace] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [renaming, setRenaming] = useState(false);

  useEffect(() => {
    setRenameValue(active?.name ?? "");
  }, [active?.id, active?.name]);

  const handleDeleteWorkspace = useCallback(async () => {
    if (!active) return;
    setDeletingWorkspace(true);
    try {
      await removeWorkspace(active.id);
      setConfirmDelete(false);
      toast.success("Workspace deleted", "Its documents and conversations are gone.");
      navigate("/app");
    } catch (cause) {
      toast.error(
        "Could not delete the workspace",
        cause instanceof Error ? cause.message : "Try again in a moment.",
      );
    } finally {
      setDeletingWorkspace(false);
    }
  }, [active, removeWorkspace, toast, navigate]);

  const handleRename = useCallback(async () => {
    const trimmed = renameValue.trim();
    if (!active || !trimmed || trimmed === active.name) return;
    setRenaming(true);
    try {
      await api.workspaces.update(active.id, { name: trimmed });
      await refreshWorkspaces();
      toast.success("Workspace renamed", "The new name is now in effect.");
    } catch {
      toast.error("Could not rename the workspace", "Try again in a moment.");
    } finally {
      setRenaming(false);
    }
  }, [active, renameValue, refreshWorkspaces, toast]);
  const [loadingModel, setLoadingModel] = useState(false);
  const [runningChecks, setRunningChecks] = useState(false);

  const providers = useAsync(() => api.settings.providers(), []);
  const rag = useAsync(() => api.settings.rag(), []);
  const system = useAsync(() => api.settings.system(), []);
  const languages = useAsync(() => api.features.languages(), []);

  const security = useAsync(() => api.evaluation.security(), []);

  const handleLoadModel = useCallback(
    async (action: "load" | "unload") => {
      setLoadingModel(true);
      try {
        const result =
          action === "load"
            ? await api.settings.loadLocalModel()
            : await api.settings.unloadLocalModel();
        toast.success(
          action === "load" ? "Local model loaded" : "Local model unloaded",
          String(result.message ?? ""),
        );
        providers.reload();
      } catch (cause) {
        toast.error(
          action === "load" ? "Could not load the model" : "Could not unload the model",
          cause instanceof ApiError ? cause.message : "Please check the server logs.",
        );
      } finally {
        setLoadingModel(false);
      }
    },
    [toast, providers],
  );

  const handleRunChecks = useCallback(async () => {
    setRunningChecks(true);
    security.reload();
    // The checks execute server-side; give them a moment before we stop spinning.
    window.setTimeout(() => setRunningChecks(false), 1200);
  }, [security]);

  const modes = providers.data?.modes;
  const engines = providers.data?.engines ?? [];
  const engineOf = (role: string) => engines.find((engine) => engine.role === role);
  const localEngine = engineOf("offline");

  return (
    <>
      <PageHeader
        title="Settings"
        description="Provider status, retrieval configuration, security self-tests and system information. Read-only from the browser by design."
        icon={<SettingsIcon className="h-4.5 w-4.5" />}
      />

      <PageBody wide className="space-y-6">
        <Tabs
          items={[
            { id: "providers", label: "AI providers", icon: <Cpu className="h-3.5 w-3.5" /> },
            { id: "retrieval", label: "Retrieval", icon: <Database className="h-3.5 w-3.5" /> },
            { id: "security", label: "Security", icon: <Shield className="h-3.5 w-3.5" /> },
            { id: "system", label: "System", icon: <Server className="h-3.5 w-3.5" /> },
            {
              id: "workspace",
              label: "Workspace",
              icon: <FolderCog className="h-3.5 w-3.5" />,
            },
          ]}
          active={tab}
          onChange={(id) => setTab(id as SettingsTab)}
        />

        {/* ================= providers ================= */}
        <TabPanel id="providers" active={tab}>
          <div className="space-y-6">
            {/* mode availability */}
            <div className="grid gap-4 lg:grid-cols-2">
              <Card className="p-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2.5">
                    <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand/10 text-brand">
                      <Cpu className="h-4 w-4" />
                    </span>
                    <div>
                      <h3 className="text-sm font-semibold text-ink">Remote answer engines</h3>
                      <p className="text-2xs text-faint">Primary · Backup fallback</p>
                    </div>
                  </div>
                  <AvailabilityPill available={Boolean(modes?.online?.available)} />
                </div>

                <p className="mt-3 text-2xs leading-relaxed text-muted">
                  {modes?.online?.available
                    ? "At least one remote engine is configured. If the primary engine is unavailable, the backup answers - and the response is labelled as a fallback."
                    : modes?.online?.reason ||
                      "No remote answer engine is configured on this server. Online mode needs one configured there."}
                </p>

                <div className="mt-3 space-y-2 border-t border-line pt-3">
                  <ProviderLine
                    label="Primary engine"
                    name={engineOf("primary")?.label ?? "Remote answer engine"}
                    configured={Boolean(engineOf("primary")?.configured)}
                  />
                  <ProviderLine
                    label="Backup engine"
                    name={engineOf("fallback")?.label ?? "Backup answer engine"}
                    configured={Boolean(engineOf("fallback")?.configured)}
                  />
                </div>
              </Card>

              <Card className="p-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2.5">
                    <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/10 text-accent">
                      <WifiOff className="h-4 w-4" />
                    </span>
                    <div>
                      <h3 className="text-sm font-semibold text-ink">Local model</h3>
                      <p className="text-2xs text-faint">
                        {/* The model NAME is the user's own configuration, so it is
                            shown; it is resolved by the backend from whatever GGUF
                            is actually present - never hard-coded here. */}
                        {localEngine?.configured && localEngine.name
                          ? localEngine.name
                          : "No local model configured"}
                      </p>
                    </div>
                  </div>
                  <AvailabilityPill available={Boolean(modes?.offline?.available)} />
                </div>

                <p className="mt-3 text-2xs leading-relaxed text-muted">
                  {localEngine?.guarantee ||
                    "When the local model is available, answers are produced entirely on this machine."}
                </p>

                <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
                  <Button
                    variant="secondary"
                    size="sm"
                    icon={<Download className="h-3.5 w-3.5" />}
                    loading={loadingModel}
                    onClick={() => void handleLoadModel("load")}
                  >
                    Load into memory
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void handleLoadModel("unload")}
                    disabled={loadingModel}
                  >
                    Unload
                  </Button>
                </div>
                <p className="mt-2 text-2xs leading-relaxed text-faint">
                  Loading pre-warms the model so the first offline answer is not slow. It
                  reserves memory until unloaded.
                </p>
              </Card>
            </div>

            {/* engine status table */}
            <Card>
              <CardHeader
                title="Answer engine status"
                description="Live availability, reported by role. Cloud implementation identity and credentials never leave the server."
                icon={<Server className="h-4 w-4" />}
                actions={
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<RefreshCw className="h-3.5 w-3.5" />}
                    onClick={providers.reload}
                  >
                    Refresh
                  </Button>
                }
              />
              {providers.loading ? (
                <LoadingPanel message="Checking engines..." />
              ) : providers.error ? (
                <div className="p-5">
                  <ErrorState message={providers.error} onRetry={providers.reload} />
                </div>
              ) : (
                <ul className="divide-y divide-line">
                  {engines.map((engine) => (
                    <li
                      key={engine.role}
                      className="flex flex-wrap items-center gap-3 px-5 py-3.5"
                    >
                      <StatusDot tone={engine.available ? "positive" : "neutral"} />

                      <div className="min-w-0 flex-1">
                        <p className="flex flex-wrap items-center gap-2 text-xs font-medium text-ink">
                          {engine.label}
                          <Badge
                            tone={
                              engine.role === "primary"
                                ? "brand"
                                : engine.role === "fallback"
                                  ? "caution"
                                  : "accent"
                            }
                          >
                            {engine.role}
                          </Badge>
                        </p>
                        {/* Only the LOCAL model shows a name - it is the user's own
                            configured file. Remote engines show none by design. */}
                        {engine.name ? (
                          <p className="mt-0.5 font-mono text-2xs text-faint">
                            {engine.name}
                          </p>
                        ) : null}
                        {engine.reason ? (
                          <p className="mt-1 text-2xs leading-relaxed text-muted">
                            {engine.reason}
                          </p>
                        ) : null}
                      </div>

                      <div className="flex shrink-0 items-center gap-1.5">
                        <Badge tone={engine.configured ? "positive" : "neutral"}>
                          {engine.configured ? "configured" : "not configured"}
                        </Badge>
                        <Badge tone={engine.available ? "positive" : "caution"}>
                          {engine.available ? "available" : "unavailable"}
                        </Badge>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <div className="border-t border-line px-5 py-3">
                <p className="flex items-start gap-2 text-2xs leading-relaxed text-faint">
                  <Info className="mt-px h-3 w-3 shrink-0" />
                  API keys live only on the server. They are never sent to the browser, never
                  included in a prompt, and never written to the trace.
                </p>
              </div>
            </Card>

            {/* appearance + languages */}
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader
                  title="Appearance"
                  description="Both themes are complete designs, not one inverted."
                  icon={<Palette className="h-4 w-4" />}
                />
                <div className="space-y-4 p-5">
                  <Segmented
                    value={theme}
                    onChange={(next) => setTheme(next)}
                    options={[
                      { value: "light", label: "Light", icon: <Sun className="h-3.5 w-3.5" /> },
                      { value: "dark", label: "Dark", icon: <Moon className="h-3.5 w-3.5" /> },
                      {
                        value: "system",
                        label: "System",
                        icon: <Monitor className="h-3.5 w-3.5" />,
                        title: "Follow the operating system setting",
                      },
                    ]}
                  />
                  <p className="text-2xs leading-relaxed text-muted">
                    Choosing "System" makes the app follow your operating system and update
                    live if it changes. Light and dark each have their own surface, border and
                    text values - dark mode is not a filter over the light one.
                  </p>
                </div>
              </Card>

              <Card>
                <CardHeader
                  title="Answer languages"
                  description="Available for translation and Read Aloud."
                  icon={<Languages className="h-4 w-4" />}
                />
                <div className="flex flex-wrap gap-1.5 p-5">
                  {(languages.data?.languages ?? []).map((language) => (
                    <Badge key={language.code} tone="neutral">
                      {language.name}
                      <span className="ml-1 font-mono text-faint">{language.code}</span>
                    </Badge>
                  ))}
                  {languages.loading ? <Spinner size={14} /> : null}
                </div>
                <div className="border-t border-line px-5 py-3">
                  <p className="text-2xs leading-relaxed text-faint">
                    Translations are validated: citations and numbers must survive, or the
                    translation is rejected and the original kept.
                  </p>
                </div>
              </Card>
            </div>
          </div>
        </TabPanel>

        {/* ================= retrieval ================= */}
        <TabPanel id="retrieval" active={tab}>
          {rag.loading ? (
            <LoadingPanel message="Loading retrieval settings…" />
          ) : rag.error ? (
            <ErrorState message={rag.error} onRetry={rag.reload} />
          ) : rag.data ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader
                  title="Chunking"
                  description="How documents are split before embedding."
                  icon={<Database className="h-4 w-4" />}
                />
                <dl className="divide-y divide-line">
                  <SettingRow
                    label="Chunk size"
                    value={`${formatNumber(rag.data.chunk_size)} characters`}
                    hint="Target length of each retrievable passage."
                  />
                  <SettingRow
                    label="Chunk overlap"
                    value={`${formatNumber(rag.data.chunk_overlap)} characters`}
                    hint="Repeated between neighbours so meaning is not cut in half."
                  />
                </dl>
              </Card>

              <Card>
                <CardHeader
                  title="Retrieval"
                  description="How many candidates are fetched and kept."
                  icon={<Database className="h-4 w-4" />}
                />
                <dl className="divide-y divide-line">
                  <SettingRow
                    label="candidate_k"
                    value={formatNumber(rag.data.candidate_k)}
                    hint="Neighbours fetched before filtering."
                  />
                  <SettingRow
                    label="top_k"
                    value={formatNumber(rag.data.top_k)}
                    hint="Excerpts placed into the model's context."
                  />
                  <SettingRow
                    label="Score threshold"
                    value={formatScore(rag.data.score_threshold)}
                    hint="Candidates below this similarity are discarded."
                  />
                  <SettingRow
                    label="Context budget"
                    value={`${formatNumber(rag.data.max_context_chars)} characters`}
                    hint="Upper bound on the evidence block."
                  />
                  <SettingRow
                    label="Re-ranking"
                    value={rag.data.rerank_enabled ? "enabled" : "disabled"}
                    hint="Off by default. It reorders candidates; it cannot recover missed evidence."
                  />
                </dl>
              </Card>

              <Card className="lg:col-span-2">
                <CardHeader
                  title="Embeddings"
                  description="The model that turns text into vectors."
                  icon={<Cpu className="h-4 w-4" />}
                />
                <dl className="divide-y divide-line">
                  <SettingRow label="Model" value={rag.data.embedding_model} mono />
                  <SettingRow
                    label="Dimensions"
                    value={String(rag.data.embedding_dim)}
                    hint="Every vector in the index has this many components."
                  />
                </dl>
                <div className="border-t border-line px-5 py-3.5">
                  <p className="flex items-start gap-2 text-2xs leading-relaxed text-faint">
                    <Info className="mt-px h-3 w-3 shrink-0" />
                    Vectors from different embedding models are not comparable. Each document
                    records the model it was embedded with, and the Knowledge Base flags any
                    that would need re-indexing after a model change.
                  </p>
                </div>
              </Card>

              <div className="lg:col-span-2 rounded-xl border border-line bg-surface p-4">
                <p className="text-2xs leading-relaxed text-muted">
                  These values are configured on the server, not here. Changing retrieval
                  parameters for a whole workspace from the browser would be easy to get
                  wrong and hard to notice. Use the{" "}
                  <span className="font-medium text-ink">Playground</span> to experiment
                  per-query instead - it applies overrides for a single run without touching
                  the stored configuration.
                </p>
              </div>
            </div>
          ) : null}
        </TabPanel>

        {/* ================= security ================= */}
        <TabPanel id="security" active={tab}>
          <div className="space-y-4">
            <Card>
              <CardHeader
                title="Security self-tests"
                description="These actually execute against the running system. A check that cannot run reports itself as skipped rather than passing by default."
                icon={<Shield className="h-4 w-4" />}
                actions={
                  <Button
                    variant="primary"
                    size="sm"
                    icon={<RefreshCw className="h-3.5 w-3.5" />}
                    loading={runningChecks}
                    onClick={() => void handleRunChecks()}
                  >
                    Run checks
                  </Button>
                }
              />

              {security.loading ? (
                <LoadingPanel message="Running security checks…" />
              ) : security.error ? (
                <div className="p-5">
                  <ErrorState message={security.error} onRetry={security.reload} />
                </div>
              ) : !security.data ? (
                <div className="p-5">
                  <EmptyState
                    icon={<Shield className="h-4.5 w-4.5" />}
                    title="No results yet"
                    description="Run the checks to see what the system can currently verify."
                  />
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-3 gap-3 border-b border-line px-5 py-4">
                    <SummaryCount
                      label="Passed"
                      value={security.data.passed}
                      tone="positive"
                      icon={<CheckCircle2 className="h-3.5 w-3.5" />}
                    />
                    <SummaryCount
                      label="Failed"
                      value={security.data.failed}
                      tone={security.data.failed > 0 ? "negative" : "neutral"}
                      icon={<XCircle className="h-3.5 w-3.5" />}
                    />
                    <SummaryCount
                      label="Skipped"
                      value={security.data.skipped}
                      tone={security.data.skipped > 0 ? "caution" : "neutral"}
                      icon={<AlertTriangle className="h-3.5 w-3.5" />}
                    />
                  </div>

                  <ul className="divide-y divide-line">
                    {(security.data.checks ?? []).map((check) => {
                      const skipped = Boolean(check.skipped);
                      return (
                        <li key={check.name} className="px-5 py-4">
                          <div className="flex items-start gap-3">
                            <span className="mt-0.5 shrink-0">
                              {skipped ? (
                                <ShieldAlert className="h-4 w-4 text-caution" />
                              ) : check.passed ? (
                                <BadgeCheck className="h-4 w-4 text-positive" />
                              ) : (
                                <XCircle className="h-4 w-4 text-negative" />
                              )}
                            </span>

                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <p className="text-xs font-medium text-ink">{check.title}</p>
                                <Badge
                                  tone={
                                    skipped ? "caution" : check.passed ? "positive" : "negative"
                                  }
                                >
                                  {skipped ? "skipped" : check.passed ? "passed" : "failed"}
                                </Badge>
                                <span className="font-mono text-2xs text-faint">{check.name}</span>
                              </div>

                              <p className="mt-1.5 text-2xs leading-relaxed text-muted">
                                {check.detail}
                              </p>

                              {check.evidence && Object.keys(check.evidence).length > 0 ? (
                                <details className="mt-2">
                                  <summary className="cursor-pointer text-2xs text-faint transition-colors hover:text-muted">
                                    Evidence
                                  </summary>
                                  <pre className="mt-2 overflow-x-auto scrollbar-thin rounded border border-line bg-sunken p-2.5 font-mono text-2xs text-muted">
                                    {JSON.stringify(check.evidence, null, 2)}
                                  </pre>
                                </details>
                              ) : null}
                            </div>
                          </div>
                        </li>
                      );
                    })}
                  </ul>

                  <div className="border-t border-line px-5 py-3.5">
                    <p className="text-2xs leading-relaxed text-faint">
                      {security.data.note ||
                        "These checks demonstrate that specific, testable properties held at the moment they ran. They do not prove the system is secure."}
                    </p>
                  </div>
                </>
              )}
            </Card>

            <div className="rounded-xl border border-line bg-surface p-5">
              <h3 className="text-sm font-semibold text-ink">How this system protects itself</h3>
              <ul className="mt-3 space-y-2.5">
                <SecurityPoint
                  title="Documents are data, never instructions"
                  body="Uploaded text is placed inside a delimited CONTEXT block and the system instruction states plainly that it is reference material. The prompt contains no secrets, so a successful injection has nothing to steal - that is the primary defence."
                />
                <SecurityPoint
                  title="Workspaces are isolated at the index"
                  body="Retrieval is filtered by workspace inside the vector store, then every returned row is re-verified, then joined against the database with the same filter. Three independent checks, and an automated test that writes a canary vector and confirms it is invisible from another workspace."
                />
                <SecurityPoint
                  title="Offline really means offline"
                  body="Offline mode has no code path to an online provider. A test blocks every socket connection and confirms that embedding and retrieval still work, proving they made no network calls."
                />
                <SecurityPoint
                  title="No stack traces reach the browser"
                  body="Errors are mapped to typed responses with a human-readable message. Internal details and file paths are stripped, and logs are scrubbed of anything shaped like a credential."
                />
              </ul>
            </div>
          </div>
        </TabPanel>

        {/* ================= system ================= */}
        <TabPanel id="system" active={tab}>
          {system.loading ? (
            <LoadingPanel message="Loading system information…" />
          ) : system.error ? (
            <ErrorState message={system.error} onRetry={system.reload} />
          ) : system.data ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader
                  title="Your data"
                  description="Counted for your account only."
                  icon={<Database className="h-4 w-4" />}
                />
                <dl className="divide-y divide-line">
                  <SettingRow label="Documents" value={formatNumber(system.data.documents)} />
                  <SettingRow label="Chunks" value={formatNumber(system.data.chunks)} />
                  <SettingRow label="Messages" value={formatNumber(system.data.messages)} />
                  <SettingRow label="Recorded queries" value={formatNumber(system.data.queries)} />
                </dl>
              </Card>

              <Card>
                <CardHeader
                  title="Storage"
                  description="Where the data actually lives."
                  icon={<Server className="h-4 w-4" />}
                />
                <dl className="divide-y divide-line">
                  <SettingRow label="Database" value={system.data.database} mono />
                  <SettingRow
                    label="Embedding model"
                    value={system.data.embedding_model}
                    mono
                  />
                  {Object.entries(system.data.storage ?? {}).map(([key, value]) => (
                    <SettingRow key={key} label={key} value={String(value)} />
                  ))}
                </dl>
              </Card>

              <Card className="lg:col-span-2">
                <CardHeader
                  title="Vector store"
                  description="Live health of the index."
                  icon={<Database className="h-4 w-4" />}
                />
                <div className="px-5 py-4">
                  <pre className="max-h-72 overflow-auto scrollbar-thin rounded-lg border border-line bg-sunken p-3.5 font-mono text-2xs leading-relaxed text-muted">
                    {JSON.stringify(system.data.vector_store, null, 2)}
                  </pre>
                </div>
              </Card>
            </div>
          ) : null}
        </TabPanel>

        {/* ================= workspace ================= */}
        <TabPanel id="workspace" active={tab}>
          <div className="space-y-6">
            <Card>
              <CardHeader
                title="Workspace"
                description="The isolated knowledge base this account is currently working in."
                icon={<FolderCog className="h-4 w-4" />}
              />
              {active ? (
                <dl className="divide-y divide-line">
                  <SettingRow label="Name" value={active.name} />
                  <SettingRow
                    label="Documents"
                    value={String(active.stats?.documents ?? 0)}
                    hint="Files indexed in this workspace."
                  />
                  <SettingRow
                    label="Chunks"
                    value={String(active.stats?.chunks ?? 0)}
                    hint="Searchable passages produced by chunking."
                  />
                  <SettingRow
                    label="Vectors"
                    value={String(active.stats?.vectors ?? 0)}
                    hint="Embeddings stored in the vector index."
                  />
                </dl>
              ) : (
                <div className="p-5">
                  <EmptyState
                    icon={<FolderCog className="h-4.5 w-4.5" />}
                    title="No workspace selected"
                    description="Choose or create a workspace to manage it here."
                  />
                </div>
              )}
            </Card>

            <Card>
              <CardHeader
                title="Workspace management"
                description="Rename the workspace. Documents and conversations are unaffected."
                icon={<Pencil className="h-4 w-4" />}
              />
              <div className="flex flex-wrap items-end gap-2.5 p-5">
                <div className="min-w-[14rem] flex-1">
                  <Input
                    label="Workspace name"
                    value={renameValue}
                    onChange={(event) => setRenameValue(event.target.value)}
                    placeholder="My workspace"
                    disabled={!active}
                  />
                </div>
                <Button
                  variant="secondary"
                  onClick={() => void handleRename()}
                  loading={renaming}
                  disabled={
                    !active || !renameValue.trim() || renameValue.trim() === active?.name
                  }
                >
                  Rename workspace
                </Button>
              </div>
            </Card>

            {/* ---- Danger Zone ---- */}
            <Card className="border-negative/35">
              <CardHeader
                title="Danger zone"
                description="Irreversible actions. There is no undo and no recycle bin."
                icon={<AlertTriangle className="h-4 w-4 text-negative" />}
              />
              <div className="flex flex-wrap items-start justify-between gap-4 p-5">
                <div className="min-w-[16rem] flex-1">
                  <p className="text-xs font-medium text-ink">Delete this workspace</p>
                  <p className="mt-1 max-w-lg text-2xs leading-relaxed text-muted">
                    Permanently deletes the workspace and everything in it. Documents in
                    other workspaces are not affected.
                  </p>
                </div>
                <Button
                  variant="danger"
                  onClick={() => setConfirmDelete(true)}
                  disabled={!active}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Delete workspace
                </Button>
              </div>
            </Card>
          </div>
        </TabPanel>
      </PageBody>

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => void handleDeleteWorkspace()}
        busy={deletingWorkspace}
        title="Delete this workspace?"
        confirmLabel="Delete workspace"
        message={
          <div className="space-y-3">
            <p>
              This permanently removes{" "}
              <strong className="text-ink">{active?.name ?? "this workspace"}</strong> and
              everything in it:
            </p>
            <ul className="space-y-1 text-2xs">
              <li>• {active?.stats?.documents ?? 0} document(s) and their stored files</li>
              <li>• {active?.stats?.chunks ?? 0} indexed chunk(s)</li>
              <li>• {active?.stats?.vectors ?? 0} embedding vector(s)</li>
              <li>• every conversation and message in this workspace</li>
              <li>• its chat-scoped document links</li>
            </ul>
            <p className="font-medium text-negative">
              This cannot be undone. There is no recycle bin.
            </p>
          </div>
        }
      />
    </>
  );
}

/* -------------------------------------------------------------------------- */

function AvailabilityPill({ available }: { available: boolean }) {
  return (
    <Badge tone={available ? "positive" : "caution"} icon={<StatusDot tone={available ? "positive" : "caution"} />}>
      {available ? "available" : "unavailable"}
    </Badge>
  );
}

function ProviderLine({
  label,
  name,
  configured,
}: {
  label: string;
  name: string;
  configured: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-2xs text-faint">{label}</span>
      <span className="flex items-center gap-2">
        <span className="font-mono text-2xs text-ink">{name}</span>
        <Badge tone={configured ? "positive" : "neutral"}>
          {configured ? "configured" : "missing key"}
        </Badge>
      </span>
    </div>
  );
}

function SettingRow({
  label,
  value,
  hint,
  mono,
}: {
  label: string;
  value: string;
  hint?: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4 px-5 py-3">
      <dt className="min-w-0 shrink-0">
        <span className="block text-xs text-ink">{label}</span>
        {hint ? (
          <span className="mt-0.5 block max-w-md text-2xs leading-relaxed text-faint">
            {hint}
          </span>
        ) : null}
      </dt>
      <dd
        className={cn(
          "min-w-0 break-words text-right text-xs text-ink",
          mono && "font-mono text-2xs",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function SummaryCount({
  label,
  value,
  tone,
  icon,
}: {
  label: string;
  value: number;
  tone: "positive" | "negative" | "caution" | "neutral";
  icon: React.ReactNode;
}) {
  return (
    <div className="text-center">
      <div
        className={cn(
          "mx-auto flex items-center justify-center gap-1.5",
          tone === "positive"
            ? "text-positive"
            : tone === "negative"
              ? "text-negative"
              : tone === "caution"
                ? "text-caution"
                : "text-muted",
        )}
      >
        {icon}
        <span className="font-display text-xl font-semibold tabular-nums">{value}</span>
      </div>
      <p className="mt-0.5 text-2xs uppercase tracking-wide text-faint">{label}</p>
    </div>
  );
}

function SecurityPoint({ title, body }: { title: string; body: string }) {
  return (
    <li className="flex items-start gap-2.5">
      <Shield className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand" />
      <div>
        <p className="text-xs font-medium text-ink">{title}</p>
        <p className="mt-0.5 text-2xs leading-relaxed text-muted">{body}</p>
      </div>
    </li>
  );
}
