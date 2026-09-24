import { useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Clock,
  Cpu,
  Database,
  FileText,
  Gauge,
  Globe,
  Layers,
  MessagesSquare,
  Quote,
  Shield,
  ShieldCheck,
  Target,
  Timer,
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
import type { DocumentUsage, StageTiming } from "@/lib/types";
import { useAsync } from "@/hooks/useAsync";
import { useWorkspaces } from "@/state/workspace";
import { NoWorkspaceNotice, PageBody, PageHeader } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader, Stat } from "@/components/ui/Card";
import { EmptyState, ErrorState, LoadingPanel } from "@/components/ui/Feedback";
import { Segmented } from "@/components/ui/Tabs";
import { AnimatedMetric } from "@/components/motion/AnimatedMetric";
import {
  BarChartH,
  DonutChart,
  LineChart,
  NoDataNote,
  type BarItem,
  type DonutSegment,
  type LineSeries,
} from "@/components/charts/Charts";

/**
 * Analytics.
 *
 * Every number on this page is a query result over recorded rows. Nothing is
 * estimated, sampled or interpolated - and where the data is absent the page
 * says so instead of drawing a zero.
 *
 * The most important part of this page is not the charts - it is the caveats.
 * The evaluation module computes honest proxies and then states what they do not
 * mean: faithfulness measures answer-to-evidence support, not truth, and answer
 * relevance is a lexical-overlap heuristic that penalises correct paraphrasing.
 * Those caveats are rendered verbatim rather than paraphrased into something
 * friendlier, because a metric presented without its limitation is misleading.
 *
 * Two details are deliberate rather than incidental:
 *
 *   - The pipeline chart is NOT sorted by duration. It is drawn in the order the
 *     pipeline actually runs, because "retrieval is 4 ms and generation is 6 s"
 *     is a fact about the architecture, and sorting it would throw that away.
 *   - A stage that did not run is not a zero-duration bar. It is a muted row
 *     saying it was not enabled, because an empty bar reads as "instant".
 */

/** Canonical verdict order. Matches the ranking the evaluation module uses. */
const GROUNDING_ORDER = [
  "SUPPORTED",
  "PARTIALLY_SUPPORTED",
  "INSUFFICIENT_EVIDENCE",
  "CITATION_ERROR",
] as const;

/** `retrieval.counts` is keyed by lower-case verdict name. */
type GroundingCounts = {
  supported: number;
  partially_supported: number;
  insufficient_evidence: number;
  citation_error: number;
};

const COUNT_KEYS: Record<(typeof GROUNDING_ORDER)[number], keyof GroundingCounts> = {
  SUPPORTED: "supported",
  PARTIALLY_SUPPORTED: "partially_supported",
  INSUFFICIENT_EVIDENCE: "insufficient_evidence",
  CITATION_ERROR: "citation_error",
};

type RangeKey = "7" | "14" | "30" | "90";

const RANGE_OPTIONS: { value: RangeKey; label: string; title: string }[] = [
  { value: "7", label: "7d", title: "Last 7 days" },
  { value: "14", label: "14d", title: "Last 14 days" },
  { value: "30", label: "30d", title: "Last 30 days" },
  { value: "90", label: "90d", title: "Last 90 days" },
];

export default function Analytics() {
  const { activeId, active, loading: workspacesLoading } = useWorkspaces();

  // One control, one window. It drives the two time-series calls only; the
  // quality and latency figures are computed over every recorded query (the API
  // takes no window for them), and the cards say so rather than implying that
  // they follow the selector.
  const [range, setRange] = useState<RangeKey>("14");
  const days = Number(range);

  const overview = useAsync(() => api.analytics.overview(activeId ?? undefined), [activeId]);
  const retrieval = useAsync(() => api.analytics.retrieval(activeId ?? undefined), [activeId]);
  const activity = useAsync(
    () => api.analytics.activity(activeId ?? undefined, days),
    [activeId, days],
  );
  const stages = useAsync(
    () => api.analytics.stages(activeId ?? undefined, days),
    [activeId, days],
  );

  const metrics = retrieval.data;
  const series = activity.data?.series ?? [];

  /** Verdict tallies, with the overview's own tally preferred over the counts. */
  const grounding = useMemo(() => {
    const counts = metrics?.counts;
    const rows = GROUNDING_ORDER.map((status) => {
      const fromOverview = Number(overview.data?.grounding?.[status] ?? 0);
      const fromCounts = Number(counts?.[COUNT_KEYS[status]] ?? 0);
      return {
        status,
        count: fromOverview > 0 ? fromOverview : fromCounts,
        styles: groundingClasses(status),
      };
    });
    const total = rows.reduce((sum, row) => sum + row.count, 0);
    return {
      rows,
      total,
      supported: rows.find((row) => row.status === "SUPPORTED")?.count ?? 0,
    };
  }, [overview.data, metrics]);

  const windowTotals = useMemo(() => {
    const totals = { queries: 0, refusals: 0, citationErrors: 0 };
    for (const point of series) {
      totals.queries += point.queries;
      totals.refusals += point.refusals;
      totals.citationErrors += point.citation_errors;
    }
    return totals;
  }, [series]);

  const questionsSeries: LineSeries[] = [
    {
      id: "queries",
      label: "Questions",
      values: series.map((point) => point.queries),
      toneClassName: "text-brand",
      area: true,
    },
    {
      id: "refusals",
      label: "Refusals",
      values: series.map((point) => point.refusals),
      toneClassName: "text-caution",
      dashed: true,
    },
  ];

  // A day with no questions has no measurable average, so it stays blank and the
  // line breaks there. Plotting it as 0 ms would claim Carinaa answered
  // instantly on a day when it was not asked anything.
  const responseTimeSeries: LineSeries[] = [
    {
      id: "avg_ms",
      label: "Average response",
      values: series.map((point) =>
        point.queries > 0 && typeof point.avg_ms === "number" && point.avg_ms > 0
          ? point.avg_ms
          : null,
      ),
      toneClassName: "text-accent",
      area: true,
    },
  ];

  // Order is the pipeline's own, straight from the API. No re-sorting.
  const stageItems: BarItem[] = (stages.data?.stages ?? []).map((stage: StageTiming) => {
    const notRun = stage.count === 0 && stage.skipped > 0;
    return {
      id: stage.stage,
      label: stage.label || stage.stage,
      value: stage.avg_ms,
      skipped: notRun,
      skippedNote: `not enabled in this run · skipped ${formatNumber(stage.skipped)}×`,
      valueLabel:
        stage.errors > 0
          ? `${formatDuration(stage.avg_ms)} · ${formatNumber(stage.errors)} failed`
          : undefined,
      toneClassName: stage.errors > 0 ? "text-caution" : "text-brand",
    };
  });

  const documentItems: BarItem[] = (metrics?.documents ?? [])
    .slice(0, 6)
    .map((document: DocumentUsage) => ({
      id: document.name,
      label: document.name,
      value: document.citations,
      valueLabel: `${formatNumber(document.citations)} ${
        document.citations === 1 ? "citation" : "citations"
      } · ${formatNumber(document.queries)} answers`,
      toneClassName: "text-brand",
    }));

  const providerEntries = Object.entries(metrics?.providers ?? {});
  const providerTotal = providerEntries.reduce((sum, [, count]) => sum + count, 0);
  const providerItems: BarItem[] = providerEntries.map(([name, count]) => ({
    id: name,
    label: providerLabel(name),
    value: count,
    valueLabel: `${formatNumber(count)} · ${formatPercent(
      providerTotal > 0 ? count / providerTotal : undefined,
      0,
    )}`,
    toneClassName: name === "local" || name === "offline" ? "text-accent" : "text-brand",
  }));

  const modeItems: BarItem[] = Object.entries(metrics?.modes ?? {}).map(([mode, count]) => ({
    id: mode,
    label: modeLabel(mode),
    value: count,
    valueLabel: formatNumber(count),
    toneClassName: mode === "offline" ? "text-accent" : "text-brand",
  }));

  const donutSegments: DonutSegment[] = grounding.rows.map((row) => ({
    id: row.status,
    label: row.styles.label,
    value: row.count,
    // groundingClasses() owns the verdict colours. The chart needs the same hue
    // as a *text* colour so `stroke-current` picks it up, hence the swap.
    toneClassName: row.styles.dot.replace(/^bg-/, "text-"),
  }));

  const queries = overview.data?.queries ?? 0;
  const avgResponseMs =
    overview.data?.avg_total_ms ?? stages.data?.avg_total_ms ?? metrics?.mean_latency_ms;
  const webSearches = overview.data?.web_searches ?? metrics?.web_searches;
  const hasQueries = (metrics?.total ?? queries) > 0;

  const retryAll = () => {
    overview.reload();
    retrieval.reload();
    activity.reload();
    stages.reload();
  };

  if (!workspacesLoading && !activeId) {
    return (
      <>
        <PageHeader
          title="Analytics"
          icon={<BarChart3 className="h-4.5 w-4.5" />}
          description="How Carinaa is being used, and how well it is answering."
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
        title="Analytics"
        description="How Carinaa is being used, and how well it is answering."
        icon={<BarChart3 className="h-4.5 w-4.5" />}
        badge={
          active ? (
            <Badge tone="accent" mono>
              {active.name}
            </Badge>
          ) : undefined
        }
        actions={
          <div className="flex items-center gap-2">
            <span className="text-2xs font-medium uppercase tracking-wide text-faint">Window</span>
            <Segmented
              options={RANGE_OPTIONS}
              value={range}
              onChange={(next) => setRange(next)}
              size="sm"
            />
          </div>
        }
      />

      <PageBody wide className="space-y-6">
        {/* ---- KPI row ------------------------------------------------
            The figures count up as the row scrolls into view. Only the number
            animates; each is passed its real value and a formatter, so the final
            rendered string is byte-for-byte what the non-animated Stat would have
            shown. A KPI that never reached its exact value would be a lie with a
            nicer entrance. */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Stat
            label="Questions asked"
            value={<AnimatedMetric value={overview.data?.queries ?? 0} format={(n) => formatNumber(Math.round(n))} />}
            hint="Recorded so they can be measured"
            icon={<MessagesSquare className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Documents"
            value={<AnimatedMetric value={overview.data?.documents ?? 0} format={(n) => formatNumber(Math.round(n))} />}
            hint={`${formatNumber(overview.data?.chunks)} chunks indexed`}
            icon={<FileText className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Average response time"
            value={<AnimatedMetric value={avgResponseMs ?? 0} format={(n) => formatDuration(Math.round(n))} />}
            hint="End to end, per question"
            icon={<Timer className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Grounded answers"
            value={
              <AnimatedMetric
                value={(grounding.total > 0 ? (grounding.supported / grounding.total) * 100 : 0)}
                format={(n) => formatPercent(n / 100, 0)}
              />
            }
            hint={`Supported, of ${formatNumber(grounding.total)} answered`}
            icon={<ShieldCheck className="h-3.5 w-3.5" />}
          />
          <Stat
            label="Web searches"
            value={<AnimatedMetric value={webSearches ?? 0} format={(n) => formatNumber(Math.round(n))} />}
            hint="Opt-in, never automatic"
            icon={<Globe className="h-3.5 w-3.5" />}
          />
        </div>

        {overview.error || retrieval.error ? (
          <ErrorState
            title="Some analytics could not be loaded"
            message={overview.error || retrieval.error}
            onRetry={retryAll}
          />
        ) : null}

        {overview.loading && !overview.data ? (
          <LoadingPanel message="Loading analytics…" />
        ) : !hasQueries ? (
          <EmptyState
            icon={<BarChart3 className="h-5 w-5" />}
            title="No activity yet"
            description="Once you ask Carinaa a few questions, your usage, timing and quality charts appear here — every number measured from your own runs."
          />
        ) : (
          <>
            {/* ---- charts --------------------------------------------- */}
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader
                  title="Questions over time"
                  description={`Questions asked per day over the last ${formatNumber(
                    days,
                  )} days, with the days Carinaa declined to answer on the same scale.`}
                  icon={<Activity className="h-4 w-4" />}
                  actions={
                    activity.loading ? (
                      <Badge tone="neutral">updating…</Badge>
                    ) : (
                      <div className="flex items-center gap-2.5">
                        <Legend
                          color="bg-brand"
                          label={`${formatNumber(windowTotals.queries)} asked`}
                        />
                        <Legend
                          color="bg-caution"
                          label={`${formatNumber(windowTotals.refusals)} refused`}
                        />
                      </div>
                    )
                  }
                />
                <div className="px-5 py-4">
                  {activity.error ? (
                    <ErrorState
                      title="Activity could not be loaded"
                      message={activity.error}
                      onRetry={activity.reload}
                    />
                  ) : activity.loading && series.length === 0 ? (
                    <LoadingPanel message="Measuring activity…" className="py-8" />
                  ) : (
                    <LineChart
                      labels={series.map((point) => point.date)}
                      series={questionsSeries}
                      ariaLabel={`Questions and refusals per day over the last ${days} days`}
                      formatValue={formatNumber}
                      height={190}
                      emptyMessage={`No questions recorded in the last ${days} days. Widen the window above, or ask something in Chat.`}
                    />
                  )}
                  {windowTotals.citationErrors > 0 ? (
                    <p className="mt-3 border-t border-line pt-3 text-2xs leading-relaxed text-muted">
                      <span className="font-mono text-caution">
                        {formatNumber(windowTotals.citationErrors)}
                      </span>{" "}
                      answers in this window referenced a source that was not in the retrieved
                      evidence.
                    </p>
                  ) : null}
                </div>
              </Card>

              <Card>
                <CardHeader
                  title="Response time by day"
                  description="Average wall-clock time for that day's questions. Days with no questions are left blank rather than plotted as zero."
                  icon={<Clock className="h-4 w-4" />}
                  actions={
                    <Badge tone="neutral" mono>
                      mean {formatDuration(avgResponseMs)}
                    </Badge>
                  }
                />
                <div className="px-5 py-4">
                  {activity.error ? (
                    <ErrorState
                      title="Activity could not be loaded"
                      message={activity.error}
                      onRetry={activity.reload}
                    />
                  ) : activity.loading && series.length === 0 ? (
                    <LoadingPanel message="Measuring response time…" className="py-8" />
                  ) : (
                    <LineChart
                      labels={series.map((point) => point.date)}
                      series={responseTimeSeries}
                      ariaLabel={`Average response time per day over the last ${days} days`}
                      formatValue={formatDuration}
                      formatAxis={formatDuration}
                      height={190}
                      emptyMessage={`No response times recorded in the last ${days} days.`}
                    />
                  )}
                </div>
              </Card>


              <Card>
                <CardHeader
                  title="Where the time goes"
                  description="Average measured time in each stage of the RAG pipeline, taken from the recorded traces."
                  icon={<Layers className="h-4 w-4" />}
                  actions={
                    stages.data?.avg_total_ms !== undefined ? (
                      <Badge tone="neutral" mono>
                        end to end {formatDuration(stages.data.avg_total_ms)}
                      </Badge>
                    ) : undefined
                  }
                />
                <div className="px-5 py-4">
                  {stages.error ? (
                    <ErrorState
                      title="Stage timings could not be loaded"
                      message={stages.error}
                      onRetry={stages.reload}
                    />
                  ) : stages.loading && !stages.data ? (
                    <LoadingPanel message="Reading stage timings…" className="py-8" />
                  ) : (
                    <BarChartH
                      items={stageItems}
                      ariaLabel={`Average milliseconds per pipeline stage over the last ${days} days`}
                      formatValue={formatDuration}
                      maxLabelChars={22}
                      emptyMessage={`No stage traces recorded in the last ${days} days yet. Ask a question and its real timings appear here.`}
                    />
                  )}
                  {stages.data?.total_queries ? (
                    <p className="mt-3 border-t border-line pt-3 text-2xs leading-relaxed text-muted">
                      Averaged over{" "}
                      <span className="font-mono text-ink">
                        {formatNumber(stages.data.total_queries)}
                      </span>{" "}
                      traced questions. Stages are listed in the order the pipeline runs them, not
                      sorted by duration - the ordering is the architecture.
                    </p>
                  ) : null}
                  {stages.data?.note ? (
                    <p className="mt-2 text-2xs leading-relaxed text-faint">{stages.data.note}</p>
                  ) : null}
                </div>
              </Card>

              <Card>
                <CardHeader
                  title="How answers were rated"
                  description="The grounding verdict recorded for every answer. Verdicts are computed by the evaluation module, not by a model judging itself."
                  icon={<Target className="h-4 w-4" />}
                  actions={
                    grounding.total > 0 ? (
                      <Badge tone="neutral" mono>
                        {formatNumber(grounding.total)} rated
                      </Badge>
                    ) : undefined
                  }
                />
                <div className="px-5 py-4">
                  {grounding.total > 0 ? (
                    <div className="flex flex-wrap items-center gap-x-6 gap-y-4">
                      <DonutChart
                        segments={donutSegments}
                        ariaLabel={`Grounding verdicts across ${formatNumber(
                          grounding.total,
                        )} answers`}
                        centreLabel="answers rated"
                        emptyMessage="No grounding verdicts recorded yet."
                      />
                      <ul className="min-w-[12rem] flex-1 space-y-2">
                        {grounding.rows.map((row) => (
                          <li key={row.status} className="flex items-center gap-2.5">
                            <span className={cn("h-2 w-2 shrink-0 rounded-full", row.styles.dot)} />
                            <span className="text-xs text-ink">{row.styles.label}</span>
                            <span className="ml-auto font-mono text-2xs tabular-nums text-muted">
                              {formatNumber(row.count)}
                            </span>
                            <span className="w-9 shrink-0 text-right font-mono text-2xs tabular-nums text-faint">
                              {formatPercent(row.count / grounding.total, 0)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : (
                    <NoDataNote>
                      No grounding verdicts have been recorded yet, so there is nothing to
                      distribute. Verdicts appear once answers have been evaluated.
                    </NoDataNote>
                  )}
                </div>
              </Card>


              <Card>
                <CardHeader
                  title="Which documents answered"
                  description="Citations per document across the recorded answers, most cited first. A document that was never cited is absent rather than shown at zero."
                  icon={<Database className="h-4 w-4" />}
                  actions={
                    documentItems.length > 0 ? (
                      <Badge tone="neutral" mono>
                        top {documentItems.length}
                      </Badge>
                    ) : undefined
                  }
                />
                <div className="px-5 py-4">
                  <BarChartH
                    items={documentItems}
                    ariaLabel="Citations recorded per document"
                    formatValue={formatNumber}
                    maxLabelChars={30}
                    emptyMessage="No citations recorded yet."
                  />
                  {documentItems.length > 0 ? (
                    <p className="mt-3 border-t border-line pt-3 text-2xs leading-relaxed text-faint">
                      Counted from the stored citations of real answers, so a gap here means the
                      document was not cited - not that it was not retrieved.
                    </p>
                  ) : null}
                </div>
              </Card>

              <Card>
                <CardHeader
                  title="Who answered"
                  description="Fallbacks are counted separately, so one provider's share is never inflated by another's failure. Local means the model ran on this machine and no question left it."
                  icon={<Cpu className="h-4 w-4" />}
                />
                <div className="space-y-4 px-5 py-4">
                  <BarChartH
                    items={providerItems}
                    ariaLabel="Answers per provider"
                    formatValue={formatNumber}
                    maxLabelChars={20}
                    emptyMessage="No provider data recorded yet."
                  />

                  {modeItems.length > 0 ? (
                    <div className="border-t border-line pt-4">
                      <p className="mb-2.5 text-2xs font-medium uppercase tracking-wide text-faint">
                        Online vs offline
                      </p>
                      <BarChartH
                        items={modeItems}
                        ariaLabel="Answers per mode"
                        formatValue={formatNumber}
                        maxLabelChars={20}
                        emptyMessage="No mode data recorded yet."
                      />
                      <div className="mt-2.5 flex items-center gap-x-4 gap-y-1">
                        <span className="flex items-center gap-1.5">
                          <WifiOff className="h-3 w-3 text-accent" />
                          <span className="text-2xs text-faint">Nothing left this machine</span>
                        </span>
                        <span className="flex items-center gap-1.5">
                          <Globe className="h-3 w-3 text-brand" />
                          <span className="text-2xs text-faint">Hosted provider</span>
                        </span>
                      </div>
                    </div>
                  ) : null}

                  {metrics?.mean_top_similarity !== undefined ? (
                    <div className="border-t border-line pt-4">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-xs text-muted">Mean top similarity</span>
                        <span className="font-mono text-2xs tabular-nums text-ink">
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


            {/* ---- answer quality and latency -------------------------- */}
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader
                  title="Answer quality"
                  description={`Derived from the grounding verdict recorded for each query, across every recorded question in this workspace - not just the ${formatNumber(
                    days,
                  )}-day window.`}
                  icon={<Shield className="h-4 w-4" />}
                  actions={
                    metrics?.total ? (
                      <Badge tone="neutral" mono>
                        {formatNumber(metrics.total)} analysed
                      </Badge>
                    ) : undefined
                  }
                />
                <div className="grid gap-3 p-5 sm:grid-cols-2">
                  <MetricTile
                    label="Faithfulness"
                    value={formatPercent(metrics?.faithfulness, 0)}
                    hint="Share of answers rated Supported"
                    tone={toneForRatio(metrics?.faithfulness)}
                  />
                  <MetricTile
                    label="Citation correctness"
                    value={formatPercent(metrics?.citation_correctness, 0)}
                    hint="Answers with no fabricated citations"
                    tone={toneForRatio(metrics?.citation_correctness)}
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
                  actions={
                    metrics?.mean_latency_ms !== undefined ? (
                      <Badge tone="neutral" mono>
                        mean {formatDuration(metrics.mean_latency_ms)}
                      </Badge>
                    ) : undefined
                  }
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
                    generation - which is why a local model feels much slower than a hosted one
                    even though the retrieval work is identical.
                  </p>
                </div>
              </Card>
            </div>


            {/* ---- recent --------------------------------------------- */}
            {metrics?.recent?.length ? (
              <Card className="overflow-hidden">
                <CardHeader
                  title="Recent queries"
                  description="The last 25 recorded questions, exactly as they were stored."
                  icon={<Quote className="h-4 w-4" />}
                />
                <div className="overflow-x-auto scrollbar-thin">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-line bg-sunken/50">
                        {[
                          "Provider",
                          "Mode",
                          "Grounding",
                          "Top score",
                          "Retrieved",
                          "Cited",
                          "Latency",
                        ].map((heading) => (
                          <th
                            key={heading}
                            className="whitespace-nowrap px-4 py-2.5 text-2xs font-semibold uppercase tracking-wide text-faint"
                          >
                            {heading}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {metrics.recent.map((row, index) => {
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
                            <td className="whitespace-nowrap px-4 py-2.5 font-mono text-2xs tabular-nums text-muted">
                              {formatScore(row.top_score)}
                            </td>
                            <td className="whitespace-nowrap px-4 py-2.5 font-mono text-2xs tabular-nums text-muted">
                              {row.retrieved}
                            </td>
                            <td className="whitespace-nowrap px-4 py-2.5 font-mono text-2xs tabular-nums text-muted">
                              {row.citations}
                            </td>
                            <td className="whitespace-nowrap px-4 py-2.5 font-mono text-2xs tabular-nums text-muted">
                              {formatDuration(row.total_ms)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>
            ) : null}


            {/* ---- caveats -------------------------------------------- */}
            <div className="rounded-xl border border-caution/30 bg-caution/8 p-4">
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-caution" />
                <div>
                  <p className="text-xs font-semibold text-caution">
                    What these numbers do not mean
                  </p>

                  {metrics?.caveats?.length ? (
                    <ul className="mt-2 space-y-1.5">
                      {metrics.caveats.map((caveat) => (
                        <li key={caveat} className="text-2xs leading-relaxed text-muted">
                          · {caveat}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-2 text-2xs leading-relaxed text-muted">
                      The evaluation module has not reported any caveats for this workspace.
                    </p>
                  )}

                  <p className="mt-2.5 text-2xs leading-relaxed text-muted">
                    The date range above applies to the activity and pipeline charts. Quality and
                    latency figures are computed over every recorded query in this workspace, so
                    they do not change when the window does.
                  </p>

                  <p className="mt-2.5 text-2xs leading-relaxed text-faint">
                    These limitations are reported by the evaluation module itself rather than
                    being smoothed over. A metric shown without its caveat is a misleading metric.
                  </p>
                </div>
              </div>
            </div>
          </>
        )}

      </PageBody>
    </>
  );
}


/* -------------------------------------------------------------------------- */
/* Small local pieces                                                          */
/* -------------------------------------------------------------------------- */

/**
 * A metric with its own label, value and one-line meaning.
 *
 * The hint is not decoration: "0.86" is meaningless without saying it is the
 * share of answers rated Supported, and a metric whose meaning is hidden is a
 * metric nobody can argue with.
 */
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

/** A dot plus label for a chart header. The count is real, never illustrative. */
function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={cn("h-2 w-2 rounded-sm", color)} />
      <span className="text-2xs text-faint">{label}</span>
    </span>
  );
}

/**
 * Providers are named by what they are, not by their internal id. "local" is the
 * one that matters: it is the difference between a question leaving the machine
 * and not leaving it.
 */
function providerLabel(name: string): string {
  if (name === "local") return "Local model (offline)";
  if (name === "offline") return "Offline";
  return name;
}

function modeLabel(mode: string): string {
  if (!mode) return "Unrecorded";
  return mode.charAt(0).toUpperCase() + mode.slice(1);
}

/**
 * Colour a ratio by its own value rather than by what we would like it to be.
 *
 * A 20% faithfulness painted green would be a claim the number does not support,
 * so the tone follows the value: green where the metric is genuinely healthy,
 * amber where it is weak, and the default ink in between. Metrics whose
 * direction is ambiguous (refusal rate, citation coverage) get no tone at all -
 * refusing when the evidence is thin is correct behaviour, not a failure.
 */
function toneForRatio(value: number | undefined): "positive" | "caution" | undefined {
  if (value === undefined || !Number.isFinite(value)) return undefined;
  if (value >= 0.8) return "positive";
  return value >= 0.5 ? undefined : "caution";
}

