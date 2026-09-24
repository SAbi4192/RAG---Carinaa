import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { useCountUp, useInView, useReducedMotion } from "@/motion";

/**
 * A metric that counts up to its value when scrolled into view.
 *
 * This is the analytics answer to the brief's request that numbers "interpolate"
 * as they enter: a KPI that is already showing its final figure before you look
 * at it teaches nothing; one that rises to its value as you reach it reads as a
 * measurement being taken. The number still lands on the EXACT value (see
 * `useCountUp`) - the animation is presentation, never a rounding of the truth.
 *
 * Reduced motion (inside useCountUp) skips the rise and shows the final value
 * immediately, so the figure is never delayed behind an effect.
 */
export function AnimatedMetric({
  value,
  format,
  durationMs = 1000,
}: {
  /** The real number. This is what counts up and what is finally displayed. */
  value: number;
  /** How to render the (possibly fractional) interpolated value. Defaults to the
   *  integer with thousands separators, matching the app's other KPI formatting. */
  format?: (n: number) => string;
  durationMs?: number;
}) {
  const [ref, inView] = useInView<HTMLSpanElement>({ threshold: 0.4 });
  const reduced = useReducedMotion();
  const display = useCountUp(Number.isFinite(value) ? value : 0, {
    active: inView,
    durationMs,
  });
  const render = format ?? ((n: number) => Math.round(n).toLocaleString());
  return (
    <span ref={ref} className="tabular-nums">
      {/* Reduced motion: the true value, immediately, with no rise and no gate on
          viewport entry. A person who asked the system not to move things should
          still never be shown a placeholder zero where their number belongs. */}
      {reduced ? render(value) : inView ? render(display) : render(0)}
    </span>
  );
}

/**
 * Reveal-on-scroll wrapper: fades and lifts its children in once, when they
 * enter the viewport. Used to give sections a sense of arrival without a
 * continuous animation the reader has to sit through. With reduced motion the
 * content is simply shown.
 */
export function Reveal({
  children,
  className,
  delayMs = 0,
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  /** Stagger within a group. Capped inside the hook usage, not here. */
  delayMs?: number;
  as?: "div" | "section" | "li" | "article";
}) {
  const [ref, inView] = useInView<HTMLDivElement>({ threshold: 0.15 });
  return (
    <Tag
      ref={ref as never}
      className={cn(
        "transition-all duration-500 ease-smooth will-change-[opacity,transform]",
        inView ? "translate-y-0 opacity-100" : "translate-y-3 opacity-0",
        className,
      )}
      style={{ transitionDelay: inView ? `${delayMs}ms` : "0ms" }}
    >
      {children}
    </Tag>
  );
}
