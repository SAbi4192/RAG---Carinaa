import { useState, type ReactNode } from "react";

import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";

/**
 * Chart primitives - hand-rolled SVG.
 *
 * There is deliberately no charting dependency in this project. The shapes we
 * need are a line, a bar and a ring, and each of them is a handful of
 * coordinates. A charting library would add a large bundle and - more to the
 * point - would want to own the colours, which would break the one rule this
 * codebase actually cares about: every colour resolves to a theme token, so
 * light and dark mode are two real designs rather than one inverted one.
 *
 * THE HONESTY RULE
 * ----------------
 * A missing measurement is never drawn as zero, because "we recorded nothing"
 * and "we recorded zero" are different claims:
 *
 *   - `null` in a series breaks the line instead of plotting a point at 0.
 *   - A stage that did not run renders as a muted "not enabled" row, not as a
 *     zero-width bar that reads as "instant".
 *   - A day with no queries is left blank rather than averaged into a 0 ms
 *     response time, which would imply the pipeline answered instantly.
 *
 * CONVENTIONS
 * -----------
 *   - Colours arrive as Tailwind classes on a wrapping `<g>` (`text-brand`), and
 *     the shapes inside draw with `stroke-current` / `fill-current`. No hex
 *     values anywhere, and dark mode needs no chart-specific code.
 *   - Each svg fills its container width and keeps its viewBox aspect ratio, so
 *     nothing needs measuring and no ResizeObserver is required.
 *   - Every chart carries `role="img"` with an `aria-label`, plus a visually
 *     hidden table of the same values: a chart is a picture of a table, and a
 *     screen-reader user is entitled to the table.
 *   - Motion is limited to a short transition on the geometry that changes when
 *     the underlying window changes. Nothing loops.
 */

/* -------------------------------------------------------------------------- */
/* Geometry                                                                    */
/* -------------------------------------------------------------------------- */

/** A single fixed viewBox width. Fixed units keep font sizes and bar heights
 *  consistent between charts that scale to different card widths. */
const WIDTH = 720;
const PAD = { top: 14, right: 14, bottom: 26, left: 54 };

type Pt = { x: number; y: number };

/**
 * Round a maximum up to a readable axis top.
 *
 * Without this, a busiest day of 7 queries produces an axis of 0 / 3.5 / 7 and
 * the gridline labels stop looking like counts.
 */
function niceCeil(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const exponent = 10 ** Math.floor(Math.log10(value));
  const fraction = value / exponent;
  const step = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return step * exponent;
}

/** "2026-09-22" -> "09-22". Anything else passes through untouched. */
function defaultLabel(label: string): string {
  const match = /^\d{4}-(\d{2})-(\d{2})$/.exec(label);
  return match ? `${match[1]}-${match[2]}` : label;
}

function truncateLabel(label: string, max: number): string {
  const clean = (label ?? "").trim();
  if (clean.length <= max) return clean;
  return `${clean.slice(0, Math.max(max - 1, 1)).trimEnd()}…`;
}

/**
 * Build a path from points, starting a NEW subpath after every gap. A single
 * unbroken path would draw a straight connector across the missing days and
 * quietly invent a trend that the data never showed.
 */
function linePath(points: (Pt | null)[]): string {
  const parts: string[] = [];
  let open = false;
  for (const point of points) {
    if (!point) {
      open = false;
      continue;
    }
    parts.push(`${open ? "L" : "M"}${point.x.toFixed(2)} ${point.y.toFixed(2)}`);
    open = true;
  }
  return parts.join(" ");
}

/** Returns null if the series has any gap - an area must not bridge missing days. */
function areaPath(points: (Pt | null)[], baseline: number): string | null {
  if (points.length < 2 || points.some((point) => point === null)) return null;
  const solid = points as Pt[];
  const head = `M${solid[0].x.toFixed(2)} ${baseline.toFixed(2)}`;
  const body = solid.map((point) => `L${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");
  const tail = `L${solid[solid.length - 1].x.toFixed(2)} ${baseline.toFixed(2)} Z`;
  return `${head} ${body} ${tail}`;
}

/* -------------------------------------------------------------------------- */
/* Shared pieces                                                               */
/* -------------------------------------------------------------------------- */

/** A muted, honest stand-in for a chart that has nothing to plot. */
export function NoDataNote({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p
      className={cn(
        "rounded-lg border border-dashed border-line bg-sunken/40 px-4 py-6 text-center text-2xs leading-relaxed text-faint",
        className,
      )}
    >
      {children}
    </p>
  );
}

/** The same numbers as visually-hidden tabular text, for screen readers. */
function SrValues({ caption, rows }: { caption: string; rows: { label: string; value: string }[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="sr-only">
      <table>
        <caption>{caption}</caption>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.label}-${index}`}>
              <th scope="row">{row.label}</th>
              <td>{row.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


/* -------------------------------------------------------------------------- */
/* LineChart                                                                   */
/* -------------------------------------------------------------------------- */

export interface LineSeries {
  id: string;
  /** Shown in the tooltip and the screen-reader table. */
  label: string;
  /** One entry per label. `null` means nothing was recorded at that point. */
  values: (number | null)[];
  /** Tailwind text-colour class, e.g. `text-brand`. Shapes draw with `currentColor`. */
  toneClassName: string;
  /** Fill the area under the line. Only drawn when the series has no gaps. */
  area?: boolean;
  /** A quieter, secondary series - dashed so it never reads as the headline. */
  dashed?: boolean;
}

export interface LineChartProps {
  labels: string[];
  series: LineSeries[];
  /** Summarises the data for screen readers. A chart without one is decoration. */
  ariaLabel: string;
  /** Formatter for tooltip and screen-reader values. */
  formatValue?: (value: number) => string;
  /** Formatter for the y-axis ticks. Defaults to `formatValue`. */
  formatAxis?: (value: number) => string;
  /** Formatter for x-axis ticks. Defaults to dropping a leading ISO year. */
  formatLabel?: (label: string) => string;
  emptyMessage?: string;
  /** Drawing-area height, in viewBox units. */
  height?: number;
  /** Upper bound on the number of x-axis ticks drawn. */
  maxTicks?: number;
  className?: string;
}

export function LineChart({
  labels,
  series,
  ariaLabel,
  formatValue = (value) => String(value),
  formatAxis,
  formatLabel = defaultLabel,
  emptyMessage = "Nothing recorded in this window yet.",
  height = 200,
  maxTicks = 7,
  className,
}: LineChartProps) {
  const [hover, setHover] = useState<number | null>(null);

  const innerW = WIDTH - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;
  const count = labels.length;

  const numbers = series.flatMap((item) =>
    item.values.filter((value): value is number => typeof value === "number" && Number.isFinite(value)),
  );
  const peak = numbers.length > 0 ? Math.max(...numbers) : 0;
  const allZero = peak <= 0;
  const yMax = allZero ? 1 : niceCeil(peak);
  const axisFormat = formatAxis ?? formatValue;

  const xFor = (index: number) =>
    count <= 1 ? PAD.left + innerW / 2 : PAD.left + (index / (count - 1)) * innerW;
  const yFor = (value: number) => PAD.top + innerH - (value / yMax) * innerH;
  const baseline = PAD.top + innerH;

  const points: (Pt | null)[][] = series.map((item) =>
    labels.map((_, index) => {
      const value = item.values[index];
      return typeof value === "number" && Number.isFinite(value)
        ? { x: xFor(index), y: yFor(value) }
        : null;
    }),
  );

  // Each hover band reaches halfway to its neighbours, so the whole plot area is
  // live and the tooltip never falls into a dead zone between two points.
  const bandFor = (index: number) => {
    if (count <= 1) return { x: PAD.left, width: innerW };
    const leftEdge = index === 0 ? PAD.left : (xFor(index - 1) + xFor(index)) / 2;
    const rightEdge =
      index === count - 1 ? PAD.left + innerW : (xFor(index) + xFor(index + 1)) / 2;
    return { x: leftEdge, width: rightEdge - leftEdge };
  };

  if (count === 0 || numbers.length === 0) {
    return (
      <div className={className}>
        <NoDataNote className="py-10">{emptyMessage}</NoDataNote>
      </div>
    );
  }

  const ticks = allZero ? [0] : [0, yMax / 2, yMax];
  const tickStride = Math.max(1, Math.ceil(count / maxTicks));
  const hoverX = hover === null ? 0 : xFor(hover);
  const tooltipShift =
    hover === 0 ? "translateX(0)" : hover === count - 1 ? "translateX(-100%)" : "translateX(-50%)";

  return (
    <div className={cn("relative", className)}>
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        className="block h-auto w-full"
        role="img"
        aria-label={ariaLabel}
        onMouseLeave={() => setHover(null)}
      >
        {/* grid and y-axis ticks */}
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={PAD.left}
              x2={PAD.left + innerW}
              y1={yFor(tick)}
              y2={yFor(tick)}
              className="stroke-line"
              strokeWidth={1}
              strokeDasharray={tick === 0 ? undefined : "2 5"}
            />
            <text
              x={PAD.left - 8}
              y={yFor(tick) + 3.5}
              textAnchor="end"
              className="fill-faint font-mono"
              fontSize={10}
            >
              {tick === 0 ? "0" : axisFormat(tick)}
            </text>
          </g>
        ))}

        {/* the series themselves */}
        {series.map((item, index) => {
          const pts = points[index];
          const drawn = pts.filter((point) => point !== null).length;
          const area = item.area ? areaPath(pts, baseline) : null;
          // Markers are dropped once there are more points than a reader can
          // tell apart; the tooltip still reports every value.
          const showMarkers = count <= 31;

          return (
            <g key={item.id} className={item.toneClassName}>
              {area ? <path d={area} className="fill-current opacity-10" /> : null}
              {drawn > 1 ? (
                <path
                  d={linePath(pts)}
                  fill="none"
                  className="stroke-current"
                  strokeWidth={item.dashed ? 1.75 : 2.25}
                  strokeDasharray={item.dashed ? "5 4" : undefined}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              ) : null}
              {showMarkers
                ? pts.map((point, pointIndex) =>
                    point ? (
                      <circle
                        key={pointIndex}
                        cx={point.x}
                        cy={point.y}
                        r={2.6}
                        className="fill-current stroke-surface"
                        strokeWidth={1.5}
                      />
                    ) : null,
                  )
                : null}
            </g>
          );
        })}

        {/* Transparent bands on top so the whole plot area responds to hover. */}
        {labels.map((label, index) => {
          const band = bandFor(index);
          return (
            <rect
              key={`band-${label}-${index}`}
              x={band.x}
              y={PAD.top}
              width={band.width}
              height={innerH}
              className="fill-transparent"
              onMouseEnter={() => setHover(index)}
            />
          );
        })}

        {hover !== null ? (
          <g>
            <line
              x1={xFor(hover)}
              x2={xFor(hover)}
              y1={PAD.top}
              y2={baseline}
              className="stroke-line-strong"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            {series.map((item, index) => {
              const point = points[index][hover];
              if (!point) return null;
              return (
                <circle
                  key={item.id}
                  cx={point.x}
                  cy={point.y}
                  r={4}
                  className={cn(item.toneClassName, "fill-current stroke-surface")}
                  strokeWidth={2}
                />
              );
            })}
          </g>
        ) : null}

        {/* x-axis ticks, thinned to keep the axis calm */}
        {labels.map((label, index) => {
          const isFirst = index === 0;
          const isLast = index === count - 1;
          if (!isFirst && !isLast && index % tickStride !== 0) return null;
          return (
            <text
              key={`tick-${label}-${index}`}
              x={xFor(index)}
              y={height - 8}
              textAnchor={isFirst && count > 1 ? "start" : isLast ? "end" : "middle"}
              className="fill-faint font-mono"
              fontSize={10}
            >
              <title>{label}</title>
              {formatLabel(label)}
            </text>
          );
        })}
      </svg>

      {hover !== null && labels[hover] !== undefined ? (
        <div
          className="pointer-events-none absolute top-0 z-10 min-w-[9.5rem] rounded-lg border border-line bg-raised px-2.5 py-2 shadow-pop"
          style={{ left: `${(hoverX / WIDTH) * 100}%`, transform: tooltipShift }}
        >
          <p className="font-mono text-2xs text-ink">{labels[hover]}</p>
          <div className="mt-1 space-y-1">
            {series.map((item) => {
              const value = item.values[hover];
              return (
                <p key={item.id} className="flex items-center gap-1.5 text-2xs">
                  <span
                    className={cn("h-1.5 w-1.5 shrink-0 rounded-full bg-current", item.toneClassName)}
                  />
                  <span className="text-muted">{item.label}</span>
                  <span className="ml-auto pl-2 font-mono tabular-nums text-ink">
                    {typeof value === "number" && Number.isFinite(value)
                      ? formatValue(value)
                      : "not recorded"}
                  </span>
                </p>
              );
            })}
          </div>
        </div>
      ) : null}

      {allZero ? (
        <p className="mt-1 text-center text-2xs text-faint">
          Every point in this window recorded zero.
        </p>
      ) : null}

      <SrValues
        caption={ariaLabel}
        rows={labels.map((label, index) => ({
          label,
          value: series
            .map((item) => {
              const value = item.values[index];
              return `${item.label}: ${
                typeof value === "number" && Number.isFinite(value) ? formatValue(value) : "not recorded"
              }`;
            })
            .join(", "),
        }))}
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* BarChartH - horizontal bars                                                 */
/* -------------------------------------------------------------------------- */

export interface BarItem {
  id: string;
  /** Full name. Truncated on the chart, complete in the tooltip and for readers. */
  label: string;
  /** Drives the bar length. Ignored (no bar is drawn) when `skipped`. */
  value: number;
  /** Replaces the trailing value text, e.g. "412 citations · 96 answers". */
  valueLabel?: string;
  /**
   * A stage that did not run. Rendered as a muted "not enabled" row instead of a
   * zero-width bar: an empty bar reads as "instant", which is a lie.
   */
  skipped?: boolean;
  /** The reason shown on a skipped row. Defaults to "not enabled in this run". */
  skippedNote?: string;
  /** Tailwind text-colour class for the bar, e.g. `text-brand`. */
  toneClassName?: string;
}

export interface BarChartHProps {
  items: BarItem[];
  ariaLabel: string;
  /** Upper bound of the scale. Defaults to the largest non-skipped value. */
  max?: number;
  formatValue?: (value: number) => string;
  /** Characters allowed before a label is truncated. */
  maxLabelChars?: number;
  emptyMessage?: string;
  className?: string;
}

const ROW_H = 34;
const BAR_H = 12;
const LABEL_COL = 178;
const VALUE_COL = 96;
const SERIES_MAX_W = WIDTH - LABEL_COL - VALUE_COL;

export function BarChartH({
  items,
  ariaLabel,
  max,
  formatValue = (value) => formatNumber(value),
  maxLabelChars = 26,
  emptyMessage = "Nothing to show yet.",
  className,
}: BarChartHProps) {
  if (items.length === 0) {
    return (
      <div className={className}>
        <NoDataNote className="py-10">{emptyMessage}</NoDataNote>
      </div>
    );
  }

  const peak = items.reduce((best, item) => (item.skipped ? best : Math.max(best, item.value)), 0);
  // An all-zero set still needs a scale to divide by; a single unit keeps the
  // geometry finite without pretending the values are anything but zero.
  const scale = max && max > 0 ? max : peak > 0 ? peak : 1;
  const height = items.length * ROW_H + 6;

  return (
    <div className={className}>
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        className="block h-auto w-full"
        role="img"
        aria-label={ariaLabel}
      >
        {items.map((item, index) => {
          const rowY = index * ROW_H + 5;
          // The bar is centred in its row so the label baseline, the bar and the
          // value all sit on one optical line.
          const barY = rowY + 11;
          const textY = barY + BAR_H - 2;
          const width = Math.max(
            (Math.max(item.value, 0) / scale) * SERIES_MAX_W,
            item.value > 0 ? 3 : 0,
          );
          const note = item.skippedNote ?? "not enabled in this run";
          const trailing = item.valueLabel ?? formatValue(item.value);

          return (
            <g key={item.id}>
              <text x={0} y={textY} className="fill-ink" fontSize={12}>
                <title>{item.label}</title>
                {truncateLabel(item.label, maxLabelChars)}
              </text>

              {item.skipped ? (
                <>
                  {/* A muted stub, so the row reads as "nothing ran here" rather
                      than as a bar of length zero. */}
                  <rect
                    x={LABEL_COL}
                    y={barY}
                    width={SERIES_MAX_W * 0.22}
                    height={BAR_H}
                    rx={BAR_H / 2}
                    className="fill-sunken"
                  />
                  <text
                    x={LABEL_COL + SERIES_MAX_W * 0.22 + 8}
                    y={textY}
                    className="fill-faint"
                    fontSize={11}
                    fontStyle="italic"
                  >
                    {note}
                  </text>
                </>
              ) : (
                <>
                  <rect
                    x={LABEL_COL}
                    y={barY}
                    width={SERIES_MAX_W}
                    height={BAR_H}
                    rx={BAR_H / 2}
                    className="fill-sunken"
                  />
                  <g className={item.toneClassName ?? "text-brand"}>
                    <rect
                      x={LABEL_COL}
                      y={barY}
                      width={width}
                      height={BAR_H}
                      rx={BAR_H / 2}
                      className="fill-current transition-[width] duration-500 ease-smooth"
                    />
                  </g>
                  <text
                    x={WIDTH}
                    y={textY}
                    textAnchor="end"
                    className="fill-muted font-mono"
                    fontSize={11}
                  >
                    {trailing}
                  </text>
                </>
              )}
            </g>
          );
        })}
      </svg>

      <SrValues
        caption={ariaLabel}
        rows={items.map((item) => ({
          label: item.label,
          value: item.skipped
            ? (item.skippedNote ?? "not enabled in this run")
            : (item.valueLabel ?? formatValue(item.value)),
        }))}
      />
    </div>
  );
}


/* -------------------------------------------------------------------------- */
/* DonutChart                                                                  */
/* -------------------------------------------------------------------------- */

export interface DonutSegment {
  id: string;
  label: string;
  value: number;
  /** Tailwind text-colour class; the ring segment draws with `stroke-current`. */
  toneClassName: string;
}

export interface DonutChartProps {
  segments: DonutSegment[];
  ariaLabel: string;
  /** Caption under the total in the middle of the ring. */
  centreLabel?: string;
  /** Rendered size in pixels. The viewBox is fixed, so only this changes. */
  size?: number;
  thickness?: number;
  emptyMessage?: string;
  className?: string;
}

export function DonutChart({
  segments,
  ariaLabel,
  centreLabel = "total",
  size = 176,
  thickness = 18,
  emptyMessage = "Nothing recorded yet.",
  className,
}: DonutChartProps) {
  const drawn = segments.filter((segment) => segment.value > 0);
  const total = drawn.reduce((sum, segment) => sum + segment.value, 0);

  if (total <= 0) {
    return (
      <div className={className}>
        <NoDataNote className="py-10">{emptyMessage}</NoDataNote>
      </div>
    );
  }

  const radius = 100 - thickness / 2 - 2;
  const circumference = 2 * Math.PI * radius;
  // A hairline gap between segments so two similar verdict colours never merge
  // into one arc. A single segment gets no gap: it would only look like a bite.
  const gap = drawn.length > 1 ? 2.5 : 0;

  let consumed = 0;
  const arcs = drawn.map((segment) => {
    const length = (segment.value / total) * circumference;
    const arc = { ...segment, dash: `${Math.max(length - gap, 0.6)} ${circumference}`, offset: -consumed };
    consumed += length;
    return arc;
  });

  return (
    <div className={cn("flex flex-wrap items-center gap-5", className)}>
      <svg
        viewBox="0 0 200 200"
        style={{ width: size, height: size }}
        className="shrink-0"
        role="img"
        aria-label={ariaLabel}
      >
        <circle
          cx={100}
          cy={100}
          r={radius}
          fill="none"
          strokeWidth={thickness}
          className="stroke-sunken"
        />
        <g transform="rotate(-90 100 100)">
          {arcs.map((arc) => (
            <circle
              key={arc.id}
              cx={100}
              cy={100}
              r={radius}
              fill="none"
              strokeWidth={thickness}
              strokeDasharray={arc.dash}
              strokeDashoffset={arc.offset}
              className={cn(
                arc.toneClassName,
                "stroke-current transition-opacity duration-200 hover:opacity-80",
              )}
            >
              <title>{`${arc.label}: ${formatNumber(arc.value)}`}</title>
            </circle>
          ))}
        </g>
        <text
          x={100}
          y={104}
          textAnchor="middle"
          className="fill-ink font-mono"
          fontSize={30}
          fontWeight={600}
        >
          {formatNumber(total)}
        </text>
        <text x={100} y={124} textAnchor="middle" className="fill-faint" fontSize={10}>
          {centreLabel}
        </text>
      </svg>

      <SrValues
        caption={ariaLabel}
        rows={segments.map((segment) => ({
          label: segment.label,
          value: `${formatNumber(segment.value)} (${
            total > 0 ? `${((segment.value / total) * 100).toFixed(0)}%` : "0%"
          })`,
        }))}
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Sparkline                                                                   */
/* -------------------------------------------------------------------------- */

export interface SparklineProps {
  values: (number | null)[];
  ariaLabel: string;
  /** Tailwind text-colour class for the line. */
  toneClassName?: string;
  height?: number;
  className?: string;
}

/**
 * A bare trend line, for sitting inside a KPI card where axes and labels would
 * be noise. Returns nothing when there is no value to draw - an empty sparkline
 * frame is worse than no sparkline at all.
 */
export function Sparkline({
  values,
  ariaLabel,
  toneClassName = "text-brand",
  height = 30,
  className,
}: SparklineProps) {
  const numbers = values.filter(
    (value): value is number => typeof value === "number" && Number.isFinite(value),
  );
  if (numbers.length === 0) return null;

  const width = 120;
  const pad = 3;
  const min = Math.min(...numbers);
  const max = Math.max(...numbers);
  const span = max - min || 1;
  const step = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0;

  const points: (Pt | null)[] = values.map((value, index) =>
    typeof value === "number" && Number.isFinite(value)
      ? {
          x: values.length > 1 ? pad + index * step : width / 2,
          y: height - pad - ((value - min) / span) * (height - pad * 2),
        }
      : null,
  );
  const onlyOne = points.filter((point) => point !== null).length === 1;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={cn("block h-auto w-full", className)}
      role="img"
      aria-label={ariaLabel}
    >
      <g className={toneClassName}>
        {!onlyOne ? (
          <path
            d={linePath(points)}
            fill="none"
            className="stroke-current"
            strokeWidth={1.75}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ) : (
          points.map((point, index) =>
            point ? (
              <circle key={index} cx={point.x} cy={point.y} r={2.5} className="fill-current" />
            ) : null,
          )
        )}
      </g>
    </svg>
  );
}

