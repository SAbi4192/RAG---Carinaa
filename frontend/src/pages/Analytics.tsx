import { useMemo } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Boxes,
  Clock,
  Cpu,
  Database,
  Gauge,
  Info,
  Quote,
  Shield,
  Target,
  WifiOff,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { api } from "@/lib/api";
import {
  formatDuration,
  formatNumber,
  formatPercent,
  formatScore,
  groundingClasses,
} from "@/lib/format";
import { useAsync } from "@/hooks/useAsync";
import { useWorkspaces } from "@/state/workspace";
import { NoWorkspaceNotice, PageBody, PageHeader } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader, Stat } from "@/components/ui/Card";
import { EmptyState, ErrorState, LoadingPanel } from "@/components/ui/Feedback";

/**
 * Analytics.
 *
 * Every number on this page is a query result over recorded rows. Nothing is
 * estimated, sampled or interpolated.
 *
 * The most important part of this page is not the charts - it is the caveats.
 * The evaluation module computes honest proxies and then states what they do not
 * mean: faithfulness measures answer-to-evidence support, not truth, and answer
 * relevance is a lexical-overlap heuristic that penalises correct paraphrasing.
 * Those caveats are rendered verbatim rather than paraphrased into something
 * friendlier, because a metric presented without its limitation is misleading.
 */

const GROUNDING_ORDER = [
  "SUPPORTED",
  "PARTIALLY_SUPPORTED",
  "INSUFFICIENT_EVIDENCE",
  "CITATION_ERROR",
];

export default function Analytics() {
  const { activeId, active, loading: workspacesLoading } = useWorkspaces();

  const overview = useAsync(() => api.analytics.overview(activeId ?? undefined), [activeId]);
  const retrieval = useAsync(() => api.analytics.retrieval(activeId ?? undefined), [activeId]);
  const activity = useAsync(() => api.analytics.activity(activeId ?? undefined, 14), [activeId]);

  const grounding = overview.data?.grounding ?? {};
  const groundingTotal = useMemo(
    () => Object.values(grounding).reduce((sum, value) => sum + Number(value || 0), 0),
    [grounding],
  );

  const maxActivity = useMemo(
    () => Math.max(...(activity.data?.series ?? []).map((point) => point.queries), 1),
    [activity.data],
  );

  if (!workspacesLoading && !activeId) {
    return (
      <>
        <PageHeader
          title="Analytics"
          icon={<BarChart3 className="h-4.5 w-4.5" />}
          description="Measured quality, latency and activity."
        />
        <PageBody>
          <NoWorkspaceNotice />
        </PageBody>
      </>
    );
  }

  const metrics = retrieval.data;
  const hasQueries = (metrics?.total ?? 0) > 0;

  return (
    <>
      <PageHeader
        title="Analytics"
        description="Measured from recorded queries. Every figure is computed from stored rows - none of it is estimated."
        icon={<BarChart3 className="h-4.5 w-4.5" />}
        badge={
          active ? (
            <Badge tone="accent" mono>
              {active.name}
            </Badge>
          ) : undefined
        }
        actions={
          metrics?.total ? (
            <Badge tone="neutral" mono>
              {formatNumber(metrics.total)} queries analysed
            </Badge>
          ) : undefined
        }
      />

      <PageBody wide className="space-y-6">
        {/* ---- inventory --------------------------------------------- */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Documents"
            value={formatNumber(overview.data?.documents ?? 0)}
            hint={`${formatNumber(overview.data?.workspaces ?? 0)} workspace(s)`}
            icon={<Database className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Chunks indexed"
            value={formatNumber(overview.data?.chunks ?? 0)}
            hint="Retrievable passages"
            icon={<Boxes className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Conversations"
            value={formatNumber(overview.data?.conversations ?? 0)}
            hint="Across this workspace"
            icon={<Activity className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Questions asked"
            value={formatNumber(overview.data?.queries ?? 0)}
            hint="Recorded for measurement"
            icon={<Target className="h-3.5 w-3.5" />}
          />
        </div>

        {!hasQueries ? (
          <EmptyState
            icon={<BarChart3 className="h-5 w-5" />}
            title="No queries recorded yet"
            description="Quality metrics are computed from real questions you have asked. Ask a few in Chat and this page will fill in - we will not show placeholder numbers in the meantime."
          />
        ) : (
          <>
            {/* ---- quality metrics --------------------------------- */}
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader
                  title="Answer quality"
                  description="Derived from the grounding verdict recorded for each query."
                  icon={<Shield className="h-4 w-4" />}
                />
                <div className="grid gap-3 p-5 sm:grid-cols-2">
                  <MetricTile
                    label="Faithfulness"
                    value={formatPercent(metrics?.faithfulness, 0)}
                    hint="Share of answers rated Supported"
                    tone="positive"
                  />
                  <MetricTile
                    label="Citation correctness"
                    value={formatPercent(metrics?.citation_correctness, 0)}
                    hint="Answers with no fabricated citations"
                    tone="positive"
                  />
                  <MetricTile
                    label="Citation coverage"
                    value={formatPercent(metrics?.citation_coverage, 0)}
                    hint="Answers that cited at least one source"
                  />
                  <MetricTile
                    label="Evidence yield"
                    value={formatPercent(metrics?.evidence_yield, 0)}
                    hint="Answers with at least one usable excerpt"
                  />
                  <MetricTile
                    label="Refusal rate"
                    value={formatPercent(metrics?.refusal_rate, 0)}
                    hint="Answers that declined to answer"
                  />
                  <MetricTile
                    label="Answer relevance"
                    value={formatPercent(metrics?.answer_relevance_proxy, 0)}
                    hint="Lexical overlap proxy - see caveats"
                  />
                </div>
              </Card>

              <Card>
                <CardHeader
                  title="Latency"
                  description="Wall-clock time for the whole pipeline, per query."
                  icon={<Clock className="h-4 w-4" />}
                />
                <div className="grid gap-3 p-5 sm:grid-cols-2">
                  <MetricTile
                    label="Median (p50)"
                    value={formatDuration(metrics?.latency?.p50_ms)}
                    hint="Half of queries were faster"
                    icon={<Gauge className="h-3.5 w-3.5" />}
                  />
                  <MetricTile
                    label="p95"
                    value={formatDuration(metrics?.latency?.p95_ms)}
                    hint="95% were faster than this"
                    icon={<Gauge className="h-3.5 w-3.5" />}
                  />
                  <MetricTile
                    label="Slowest"
                    value={formatDuration(metrics?.latency?.max_ms)}
                    hint="Single worst case"
                  />
                  <MetricTile
                    label="Retrieval only (p50)"
                    value={formatDuration(metrics?.latency?.retrieval_p50_ms)}
                    hint="Excludes generation"
                  />
                </div>

                <div className="border-t border-line px-5 py-3.5">
                  <p className="text-2xs leading-relaxed text-faint">
                    Retrieval is typically a small fraction of total latency. The rest is
                    generation - which is why a local model feels much slower than a hosted
                    one even though the retrieval work is identical.
                  </p>
                </div>
              </Card>
            </div>

            {/* ---- grounding distribution -------------------------- */}
            <Card>
              <CardHeader
                title="Grounding verdicts"
                description="How the answers this workspace produced were rated."
                icon={<Shield className="h-4 w-4" />}
                actions={
                  <Badge tone="neutral" mono>
                    {formatNumber(groundingTotal)} total
                  </Badge>
                }
              />
              <div className="space-y-3 p-5">
                {GROUNDING_ORDER.map((status) => {
                  const count = Number(grounding[status] ?? 0);
                  const share = groundingTotal > 0 ? count / groundingTotal : 0;
                  const styles = groundingClasses(status);

                  return (
                    <div key={status}>
                      <div className="flex items-center justify-between gap-3">
                        <span className="flex items-center gap-2">
                          <span className={cn("h-2 w-2 rounded-full", styles.dot)} />
                          <span className="text-xs text-ink">{styles.label}</span>
                        </span>
                        <span className="flex items-center gap-2">
                          <span className="font-mono text-2xs text-muted">
                            {formatNumber(count)}
                          </span>
                          <span className="w-10 text-right font-mono text-2xs text-faint">
                            {formatPercent(share, 0)}
                          </span>
                        </span>
                      </div>
                      <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-sunken">
                        <div
                          className={cn("h-full rounded-full transition-[width] duration-700", styles.dot)}
                          style={{ width: `${Math.max(share * 100, count > 0 ? 1.5 : 0)}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </Card>

            {/* ---- activity chart ---------------------------------- */}
            <Card>
              <CardHeader
                title="Activity"
                description="Questions asked per day over the last 14 days."
                icon={<Activity className="h-4 w-4" />}
                actions={
                  <div className="flex items-center gap-2.5">
                    <Legend color="bg-brand" label="Queries" />
                    <Legend color="bg-caution" label="Refusals" />
                    <Legend color="bg-negative" label="Citation errors" />
                  </div>
                }
              />
              <div className="p-5">
                <div className="flex h-36 items-end gap-1.5">
                  {(activity.data?.series ?? []).map((point) => {
                    const height = (point.queries / maxActivity) * 100;
                    return (
                      <div
                        key={point.date}
                        className="group relative flex flex-1 flex-col items-center justify-end"
                        style={{ minWidth: 0 }}
                      >
                        <div className="relative flex w-full flex-col justify-end" style={{ height: "100%" }}>
                          <div
                            className="w-full rounded-t bg-brand/70 transition-colors group-hover:bg-brand"
                            style={{ height: `${Math.max(height, point.queries > 0 ? 4 : 0)}%` }}
                          />
                        </div>

                        {/* tooltip */}
                        <div className="pointer-events-none absolute bottom-full z-10 mb-2 hidden whitespace-nowrap rounded-lg border border-line bg-raised px-2.5 py-1.5 shadow-pop group-hover:block">
                          <p className="font-mono text-2xs text-ink">{point.date}</p>
                          <p className="text-2xs text-muted">
                            {point.queries} queries
                            {point.refusals > 0 ? ` · ${point.refusals} refused` : ""}
                            {point.citation_errors > 0
                              ? ` · ${point.citation_errors} citation errors`
                              : ""}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="mt-2 flex justify-between">
                  <span className="font-mono text-2xs text-faint">
                    {activity.data?.series[0]?.date ?? ""}
                  </span>
                  <span className="font-mono text-2xs text-faint">
                    {activity.data?.series[activity.data.series.length - 1]?.date ?? ""}
                  </span>
                </div>
              </div>
            </Card>

            {/* ---- providers & modes ------------------------------- */}
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader
                  title="Which provider answered"
                  description="Fallbacks are counted separately, so a provider's share is never inflated by another's failure."
                  icon={<Cpu className="h-4 w-4" />}
                />
                <div className="space-y-2.5 p-5">
                  {Object.entries(metrics?.providers ?? {}).length === 0 ? (
                    <p className="text-2xs text-muted">No provider data yet.</p>
                  ) : (
                    Object.entries(metrics?.providers ?? {}).map(([provider, count]) => (
                      <div key={provider} className="flex items-center justify-between gap-3">
                        <span className="flex items-center gap-2 text-xs text-ink">
                          {provider === "local" ? (
                            <WifiOff className="h-3.5 w-3.5 text-accent" />
                          ) : (
                            <Cpu className="h-3.5 w-3.5 text-brand" />
                          )}
                          {provider}
                        </span>
                        <span className="font-mono text-2xs text-muted">{formatNumber(count)}</span>
                      </div>
                    ))
                  )}
                </div>
              </Card>

              <Card>
                <CardHeader
                  title="Online vs offline"
                  description="How often each mode was used."
                  icon={<Target className="h-4 w-4" />}
                />
                <div className="space-y-2.5 p-5">
                  {Object.entries(metrics?.modes ?? {}).length === 0 ? (
                    <p className="text-2xs text-muted">No mode data yet.</p>
                  ) : (
                    Object.entries(metrics?.modes ?? {}).map(([mode, count]) => (
                      <div key={mode} className="flex items-center justify-between gap-3">
                        <span className="flex items-center gap-2 text-xs text-ink">
                          {mode === "offline" ? (
                            <WifiOff className="h-3.5 w-3.5 text-accent" />
                          ) : (
                            <Cpu className="h-3.5 w-3.5 text-brand" />
                          )}
                          {mode}
                        </span>
                        <span className="font-mono text-2xs text-muted">{formatNumber(count)}</span>
                      </div>
                    ))
                  )}
                  {metrics?.mean_top_similarity !== undefined ? (
                    <div className="mt-3 border-t border-line pt-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-xs text-muted">Mean top similarity</span>
                        <span className="font-mono text-2xs text-ink">
                          {formatScore(metrics.mean_top_similarity)}
                        </span>
                      </div>
                      <p className="mt-1.5 text-2xs leading-relaxed text-faint">
                        The average best-match score across all queries. A sustained low value
                        suggests your questions are drifting away from what is indexed.
                      </p>
                    </div>
                  ) : null}
                </div>
              </Card>
            </div>

            {/* ---- recent ----------------------------------------- */}
            <Card className="overflow-hidden">
              <CardHeader
                title="Recent queries"
                description="The last 25 recorded questions."
                icon={<Quote className="h-4 w-4" />}
              />
              <div className="overflow-x-auto scrollbar-thin">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-line bg-sunken/50">
                      {["Provider", "Mode", "Grounding", "Top score", "Retrieved", "Cited", "Latency"].map(
                        (heading) => (
                          <th
                            key={heading}
                            className="whitespace-nowrap px-4 py-2.5 text-2xs font-semibold uppercase tracking-wide text-faint"
                          >
                            {heading}
                          </th>
                        ),
                      )}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {(metrics?.recent ?? []).map((row, index) => {
                      const styles = groundingClasses(row.grounding_status);
                      return (
                        <tr key={index} className="transition-colors hover:bg-sunken/40">
                          <td className="whitespace-nowrap px-4 py-2.5 font-mono text-2xs text-ink">
                            {row.provider || "—"}
                          </td>
                          <td className="whitespace-nowrap px-4 py-2.5 text-2xs text-muted">
                            {row.ai_mode}
                          </td>
                          <td className="whitespace-nowrap px-4 py-2.5">
                            <span className="inline-flex items-center gap-1.5">
                              <span className={cn("h-1.5 w-1.5 rounded-full", styles.dot)} />
                              <span className="text-2xs text-muted">{styles.label}</span>
                            </span>
                          </td>
                          <td className="whitespace-nowrap px-4 py-2.5 font-mono text-2xs text-muted">
                            {formatScore(row.top_score)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-2.5 font-mono text-2xs text-muted">
                            {row.retrieved}
                          </td>
                          <td className="whitespace-nowrap px-4 py-2.5 font-mono text-2xs text-muted">
                            {row.citations}
                          </td>
                          <td className="whitespace-nowrap px-4 py-2.5 font-mono text-2xs text-muted">
                            {formatDuration(row.total_ms)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>

            {/* ---- caveats ---------------------------------------- */}
            {metrics?.caveats?.length ? (
              <div className="rounded-xl border border-caution/30 bg-caution/8 p-4">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-caution" />
                  <div>
                    <p className="text-xs font-semibold text-caution">
                      What these metrics do not tell you
                    </p>
                    <ul className="mt-2 space-y-1.5">
                      {metrics.caveats.map((caveat) => (
                        <li key={caveat} className="text-2xs leading-relaxed text-muted">
                          · {caveat}
                        </li>
                      ))}
                    </ul>
                    <p className="mt-2.5 text-2xs leading-relaxed text-faint">
                      These limitations are reported by the evaluation module itself rather
                      than being smoothed over. A metric shown without its caveat is a
                      misleading metric.
                    </p>
                  </div>
                </div>
              </div>
            ) : null}
          </>
        )}

        {overview.error || retrieval.error ? (
          <ErrorState
            title="Some analytics could not be loaded"
            message={overview.error || retrieval.error}
            onRetry={() => {
              overview.reload();
              retrieval.reload();
              activity.reload();
            }}
          />
        ) : null}

        {overview.loading && !overview.data ? <LoadingPanel message="Loading analytics…" /> : null}
      </PageBody>
    </>
  );
}

/* -------------------------------------------------------------------------- */

function MetricTile({
  label,
  value,
  hint,
  tone,
  icon,
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "positive" | "caution" | "negative";
  icon?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-line bg-sunken/50 p-3.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-2xs font-medium uppercase tracking-wide text-faint">{label}</p>
        {icon ? <span className="text-faint">{icon}</span> : null}
      </div>
      <p
        className={cn(
          "mt-1.5 font-display text-xl font-semibold tabular-nums",
          tone === "positive"
            ? "text-positive"
            : tone === "caution"
              ? "text-caution"
              : tone === "negative"
                ? "text-negative"
                : "text-ink",
        )}
      >
        {value}
      </p>
      <p className="mt-0.5 text-2xs leading-relaxed text-muted">{hint}</p>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={cn("h-2 w-2 rounded-sm", color)} />
      <span className="text-2xs text-faint">{label}</span>
    </span>
  );
}

export { Info };
