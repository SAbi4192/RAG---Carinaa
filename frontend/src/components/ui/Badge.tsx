import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Badge.
 *
 * Used for statuses, file types, counts and provenance chips. `mono` exists
 * because provenance values (`p.32`, `Sheet1, rows 4-9`, `$.servers[0].cpu`) are
 * data, not prose, and lining them up in a monospaced face makes them scannable.
 */

export type BadgeTone =
  | "neutral"
  | "brand"
  | "accent"
  | "positive"
  | "caution"
  | "negative"
  | "info";

const TONES: Record<BadgeTone, string> = {
  neutral: "border-line bg-sunken text-muted",
  brand: "border-brand/25 bg-brand/10 text-brand",
  accent: "border-accent/25 bg-accent/10 text-accent",
  positive: "border-positive/30 bg-positive/10 text-positive",
  caution: "border-caution/30 bg-caution/10 text-caution",
  negative: "border-negative/30 bg-negative/10 text-negative",
  info: "border-info/30 bg-info/10 text-info",
};

export function Badge({
  tone = "neutral",
  mono,
  icon,
  children,
  className,
  title,
}: {
  tone?: BadgeTone;
  mono?: boolean;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-2xs font-medium",
        mono && "font-mono tracking-tight",
        TONES[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

/**
 * A small coloured dot plus label. Used for provider status and grounding
 * verdicts, where the dot carries the meaning and the text confirms it.
 */
export function StatusDot({
  tone = "neutral",
  pulse,
  className,
}: {
  tone?: BadgeTone;
  pulse?: boolean;
  className?: string;
}) {
  const color =
    tone === "positive"
      ? "bg-positive"
      : tone === "caution"
        ? "bg-caution"
        : tone === "negative"
          ? "bg-negative"
          : tone === "brand"
            ? "bg-brand"
            : tone === "accent"
              ? "bg-accent"
              : tone === "info"
                ? "bg-info"
                : "bg-faint";

  return (
    <span className={cn("relative inline-flex h-2 w-2 shrink-0", className)}>
      {pulse ? (
        <span className={cn("absolute inset-0 animate-pulse-ring rounded-full", color)} />
      ) : null}
      <span className={cn("relative inline-flex h-2 w-2 rounded-full", color)} />
    </span>
  );
}

/** Keyboard key hint, e.g. for ⌘K. */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-line bg-sunken px-1.5 py-0.5 font-mono text-2xs text-muted">
      {children}
    </kbd>
  );
}
