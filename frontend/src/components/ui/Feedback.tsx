import type { ReactNode } from "react";
import { AlertCircle, Loader2, RefreshCw } from "lucide-react";

import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";
import { formatPercentRaw } from "@/lib/format";

/**
 * Loading, empty and error states.
 *
 * These are not afterthoughts. Most of what a user sees in a data-heavy app is
 * one of these three states, and a blank screen with no explanation is the single
 * most common way a project like this feels unfinished. Every list, panel and
 * fetch in Carinaa renders one of these deliberately.
 *
 * The rule: an empty state must say WHY it is empty and WHAT to do next. "No
 * documents" is not an empty state; "No documents yet - upload one to get
 * started" is.
 */

/* -------------------------------------------------------------------------- */
/* Spinner                                                                     */
/* -------------------------------------------------------------------------- */
export function Spinner({ className, size = 16 }: { className?: string; size?: number }) {
  return (
    <Loader2
      className={cn("animate-spin text-muted", className)}
      style={{ width: size, height: size }}
      aria-hidden
    />
  );
}

/** Full-panel centred spinner with a message. */
export function LoadingPanel({
  message = "Loading…",
  className,
}: {
  message?: string;
  className?: string;
}) {
  return (
    <div
      className={cn("flex flex-col items-center justify-center gap-3 py-14", className)}
      role="status"
      aria-live="polite"
    >
      <Spinner size={22} />
      <p className="text-xs text-muted">{message}</p>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Skeletons                                                                   */
/* -------------------------------------------------------------------------- */
export function SkeletonLine({ className }: { className?: string }) {
  return <div className={cn("skeleton h-3.5 w-full", className)} />;
}

export function SkeletonCard({ lines = 3 }: { lines?: number }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-5 shadow-card">
      <SkeletonLine className="w-1/3" />
      <div className="mt-4 space-y-2.5">
        {Array.from({ length: lines }).map((_, index) => (
          <SkeletonLine key={index} className={index === lines - 1 ? "w-2/3" : "w-full"} />
        ))}
      </div>
    </div>
  );
}

/** A list of skeleton rows, for tables. */
export function SkeletonRows({ rows = 5, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)}>
      {Array.from({ length: rows }).map((_, index) => (
        <div
          key={index}
          className="flex items-center gap-4 rounded-lg border border-line bg-surface px-4 py-3"
        >
          <div className="skeleton h-8 w-8 rounded-lg" />
          <SkeletonLine className="flex-1" />
          <SkeletonLine className="w-16" />
        </div>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Empty state                                                                 */
/* -------------------------------------------------------------------------- */
export function EmptyState({
  icon,
  title,
  description,
  action,
  secondaryAction,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  secondaryAction?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-xl border border-dashed border-line-strong bg-surface/50 px-6 py-12 text-center",
        className,
      )}
    >
      {icon ? (
        <div className="mb-3.5 flex h-11 w-11 items-center justify-center rounded-xl border border-line bg-sunken text-faint">
          {icon}
        </div>
      ) : null}
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {description ? (
        <p className="mt-1.5 max-w-md text-xs leading-relaxed text-muted">{description}</p>
      ) : null}
      {action || secondaryAction ? (
        <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
          {action}
          {secondaryAction}
        </div>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Error state                                                                 */
/* -------------------------------------------------------------------------- */
export function ErrorState({
  title = "Something went wrong",
  message,
  detail,
  onRetry,
  className,
}: {
  title?: string;
  message?: string;
  detail?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center rounded-xl border border-negative/30 bg-negative/5 px-6 py-10 text-center",
        className,
      )}
    >
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl border border-negative/25 bg-negative/10 text-negative">
        <AlertCircle className="h-5 w-5" />
      </div>
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {message ? (
        <p className="mt-1.5 max-w-lg text-xs leading-relaxed text-muted">{message}</p>
      ) : null}
      {detail ? (
        <p className="mt-2 max-w-lg rounded-lg border border-line bg-sunken px-3 py-2 font-mono text-2xs text-faint">
          {detail}
        </p>
      ) : null}
      {onRetry ? (
        <Button
          variant="secondary"
          size="sm"
          className="mt-4"
          icon={<RefreshCw className="h-3.5 w-3.5" />}
          onClick={onRetry}
        >
          Try again
        </Button>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Progress                                                                    */
/* -------------------------------------------------------------------------- */
/**
 * A determinate progress bar.
 *
 * Always driven by a real value. There is deliberately no indeterminate variant
 * that animates forever, because that is exactly the "fake progress" this project
 * is required to avoid - if we do not know the percentage, we show a spinner
 * instead and say what stage we are on.
 */
export function ProgressBar({
  value,
  tone = "brand",
  showLabel,
  className,
  height = "md",
}: {
  /** Percentage, 0-100. */
  value: number;
  tone?: "brand" | "positive" | "caution" | "negative";
  showLabel?: boolean;
  className?: string;
  height?: "sm" | "md";
}) {
  const clamped = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));

  const barColor =
    tone === "positive"
      ? "bg-positive"
      : tone === "caution"
        ? "bg-caution"
        : tone === "negative"
          ? "bg-negative"
          : "bg-brand";

  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <div
        className={cn(
          "relative flex-1 overflow-hidden rounded-full bg-sunken",
          height === "sm" ? "h-1" : "h-1.5",
        )}
        role="progressbar"
        aria-valuenow={Math.round(clamped)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={cn("h-full rounded-full transition-[width] duration-500 ease-smooth", barColor)}
          style={{ width: `${clamped}%` }}
        />
      </div>
      {showLabel ? (
        <span className="w-9 shrink-0 text-right font-mono text-2xs tabular-nums text-muted">
          {formatPercentRaw(clamped)}
        </span>
      ) : null}
    </div>
  );
}
