import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Tabs.
 *
 * Implemented with `role="tablist"` and arrow-key navigation, because a row of
 * buttons that looks like tabs but does not respond to arrow keys is a common and
 * irritating accessibility failure.
 */

export interface TabItem {
  id: string;
  label: ReactNode;
  icon?: ReactNode;
  /** Optional count shown as a superscript, e.g. chunks in a document. */
  count?: number;
  disabled?: boolean;
}

export function Tabs({
  items,
  active,
  onChange,
  className,
  size = "md",
}: {
  items: TabItem[];
  active: string;
  onChange: (id: string) => void;
  className?: string;
  size?: "sm" | "md";
}) {
  const handleKeyDown = (event: React.KeyboardEvent, index: number) => {
    const enabled = items.filter((item) => !item.disabled);
    const current = enabled.findIndex((item) => item.id === items[index].id);
    if (current < 0) return;

    let nextIndex = current;
    if (event.key === "ArrowRight") nextIndex = (current + 1) % enabled.length;
    else if (event.key === "ArrowLeft") nextIndex = (current - 1 + enabled.length) % enabled.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = enabled.length - 1;
    else return;

    event.preventDefault();
    onChange(enabled[nextIndex].id);
  };

  return (
    <div
      role="tablist"
      className={cn(
        "flex items-center gap-1 overflow-x-auto border-b border-line scrollbar-thin",
        className,
      )}
    >
      {items.map((item, index) => {
        const selected = item.id === active;
        return (
          <button
            key={item.id}
            role="tab"
            type="button"
            aria-selected={selected}
            aria-controls={`panel-${item.id}`}
            id={`tab-${item.id}`}
            tabIndex={selected ? 0 : -1}
            disabled={item.disabled}
            onClick={() => onChange(item.id)}
            onKeyDown={(event) => handleKeyDown(event, index)}
            className={cn(
              "relative inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap font-medium transition-colors duration-150",
              "disabled:cursor-not-allowed disabled:opacity-40",
              size === "sm" ? "px-2.5 py-2 text-2xs" : "px-3 py-2.5 text-xs",
              selected ? "text-ink" : "text-muted hover:text-ink",
            )}
          >
            {item.icon}
            {item.label}
            {typeof item.count === "number" ? (
              <span
                className={cn(
                  "ml-0.5 rounded px-1 py-px font-mono text-2xs tabular-nums",
                  selected ? "bg-brand/10 text-brand" : "bg-sunken text-faint",
                )}
              >
                {item.count}
              </span>
            ) : null}

            {/* The underline is an absolutely positioned element rather than a
                border, so it can animate and so it does not shift the layout. */}
            {selected ? (
              <span className="absolute inset-x-1.5 -bottom-px h-0.5 rounded-full bg-brand" />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** Wrapper that pairs with a tab id for `aria-controls`. */
export function TabPanel({
  id,
  active,
  children,
  className,
}: {
  id: string;
  active: string;
  children: ReactNode;
  className?: string;
}) {
  if (id !== active) return null;
  return (
    <div
      role="tabpanel"
      id={`panel-${id}`}
      aria-labelledby={`tab-${id}`}
      className={cn("animate-fade-in", className)}
    >
      {children}
    </div>
  );
}

/**
 * Segmented control - a compact alternative to tabs for switching a mode rather
 * than navigating content (e.g. Online / Offline).
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  className,
  size = "md",
}: {
  options: { value: T; label: ReactNode; icon?: ReactNode; title?: string }[];
  value: T;
  onChange: (next: T) => void;
  className?: string;
  size?: "sm" | "md";
}) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-0.5 rounded-lg border border-line bg-sunken p-0.5",
        className,
      )}
      role="radiogroup"
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            title={option.title}
            onClick={() => onChange(option.value)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-[0.4rem] font-medium transition-all duration-150",
              size === "sm" ? "px-2 py-1 text-2xs" : "px-2.5 py-1.5 text-xs",
              selected
                ? "bg-surface text-ink shadow-card"
                : "text-muted hover:text-ink",
            )}
          >
            {option.icon}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
