import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Card and its parts.
 *
 * `Card` is deliberately a plain surface with no padding, and the padding lives
 * in `CardHeader` / `CardBody` / `CardFooter`. That way a card containing a table
 * can have a flush body without fighting the component's own padding.
 */

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Adds hover lift. Only use when the whole card is clickable. */
  interactive?: boolean;
  padded?: boolean;
}

export function Card({ className, interactive, padded, ...rest }: CardProps) {
  return (
    <div
      className={cn(
        "rounded-xl border border-line bg-surface shadow-card",
        padded && "p-5",
        interactive &&
          "cursor-pointer transition-all duration-200 ease-smooth hover:-translate-y-0.5 hover:border-line-strong hover:shadow-lifted",
        className,
      )}
      {...rest}
    />
  );
}

export function CardHeader({
  title,
  description,
  actions,
  icon,
  className,
  children,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 border-b border-line px-5 py-4",
        className,
      )}
    >
      <div className="flex min-w-0 items-start gap-3">
        {icon ? <div className="mt-0.5 shrink-0 text-muted">{icon}</div> : null}
        <div className="min-w-0">
          {title ? (
            <h3 className="truncate text-sm font-semibold text-ink">{title}</h3>
          ) : null}
          {description ? (
            <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p>
          ) : null}
          {children}
        </div>
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function CardBody({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 py-4", className)} {...rest} />;
}

export function CardFooter({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 border-t border-line px-5 py-3",
        className,
      )}
      {...rest}
    />
  );
}

/**
 * A labelled statistic. Used across the Dashboard and Analytics so the numbers
 * are always presented identically.
 */
export function Stat({
  label,
  value,
  hint,
  icon,
  tone,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  tone?: "default" | "positive" | "caution" | "negative" | "brand";
  className?: string;
}) {
  const toneClass =
    tone === "positive"
      ? "text-positive"
      : tone === "caution"
        ? "text-caution"
        : tone === "negative"
          ? "text-negative"
          : tone === "brand"
            ? "text-brand"
            : "text-ink";

  return (
    <div className={cn("rounded-xl border border-line bg-surface p-4 shadow-card", className)}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-2xs font-medium uppercase tracking-wide text-faint">{label}</p>
        {icon ? <span className="text-faint">{icon}</span> : null}
      </div>
      <p className={cn("mt-2 font-display text-2xl font-semibold tabular-nums", toneClass)}>
        {value}
      </p>
      {hint ? <p className="mt-1 text-xs text-muted">{hint}</p> : null}
    </div>
  );
}
