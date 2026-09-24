/**
 * The Carinaa motion system.
 *
 * A small set of REUSABLE primitives, not animation code scattered per page.
 * Everything here respects `prefers-reduced-motion` in the same way: the hook
 * returns a value the caller branches on, so a reduced-motion user still gets
 * the final state - they never miss information, only the movement.
 *
 * These are deliberately hook-and-CSS, not an animation library. The app's
 * motion needs are entrance reveals, count-ups, and reordering existing DOM
 * nodes; a dependency that ships a physics engine for that would add weight and
 * a second mental model for no gain, and the brief warns against exactly that.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/* -------------------------------------------------------------------------- */
/* Reduced motion                                                             */
/* -------------------------------------------------------------------------- */

/**
 * Live `prefers-reduced-motion` state. Reads the real media query and updates
 * if the user changes the OS setting mid-session (some people do, and a page
 * that only checked at mount would keep animating at their worst moment).
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => readReducedMotion());
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

function readReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** The motion duration a component should use: 0 for reduced-motion users. */
export function motionMs(reduced: boolean, normal: number): number {
  return reduced ? 0 : normal;
}

/* -------------------------------------------------------------------------- */
/* Viewport entry                                                             */
/* -------------------------------------------------------------------------- */

/**
 * Fires once when the element scrolls into view. Used to start entrance
 * animations (bars grow, lines draw, numbers count) the moment a section
 * becomes visible - which is the difference between a dashboard that tells a
 * story as you read it and one that finished animating off-screen behind you.
 *
 * One-shot by default: a chart that replays its draw animation every time you
 * scroll past it is not informative, it is tiring.
 */
export function useInView<T extends HTMLElement = HTMLDivElement>(options?: {
  threshold?: number;
  once?: boolean;
  rootMargin?: string;
}): [React.RefObject<T>, boolean] {
  const { threshold = 0.25, once = true, rootMargin = "0px 0px -8% 0px" } = options ?? {};
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (typeof IntersectionObserver === "undefined") {
      // No observer support: show the final state immediately. Never hide
      // content behind an animation the browser cannot run.
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (!entry) return;
        if (entry.isIntersecting) {
          setInView(true);
          if (once) observer.disconnect();
        } else if (!once) {
          setInView(false);
        }
      },
      { threshold, rootMargin },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [threshold, once, rootMargin]);

  return [ref, inView];
}

/* -------------------------------------------------------------------------- */
/* Count up                                                                   */
/* -------------------------------------------------------------------------- */

/**
 * Interpolate a number from 0 (or `from`) to `value` when `active` is true.
 *
 * This drives the analytics KPIs. Two honesty rules it enforces:
 *   - it always lands on the EXACT target value. An interpolated display that
 *     settled on 1,998 instead of 2,000 would be a rounded-looking wrong number.
 *   - when reduced motion is on, it returns the final value with no animation
 *     at all. The number is never delayed behind an effect.
 */
export function useCountUp(
  value: number,
  options?: { durationMs?: number; active?: boolean; from?: number },
): number {
  const { durationMs = 900, active = true, from = 0 } = options ?? {};
  const reduced = useReducedMotion();
  const [display, setDisplay] = useState(reduced || !active ? value : from);
  const frame = useRef<number | null>(null);
  const start = useRef<number | null>(null);

  useEffect(() => {
    if (!active) return;
    if (reduced || durationMs <= 0) {
      setDisplay(value);
      return;
    }
    start.current = null;
    const step = (timestamp: number) => {
      if (start.current === null) start.current = timestamp;
      const progress = Math.min(1, (timestamp - start.current) / durationMs);
      // ease-out cubic: fast at the start, settling gently. A linear count reads
      // as a mechanical clock; an ease-out reads as a number arriving at rest.
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(progress >= 1 ? value : from + (value - from) * eased);
      if (progress < 1) frame.current = requestAnimationFrame(step);
    };
    frame.current = requestAnimationFrame(step);
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [value, active, reduced, durationMs, from]);

  return display;
}

/* -------------------------------------------------------------------------- */
/* FLIP reordering                                                            */
/* -------------------------------------------------------------------------- */

/**
 * Animate list reordering with FLIP (First, Last, Invert, Play).
 *
 * WHY THIS EXISTS AND WHY IT IS NOT CSS. When the Retrieval Lab switches from
 * dense to hybrid, the SAME chunks move to DIFFERENT positions. CSS cannot
 * express "you are now the fourth item" as a transition - `top`/`order` changes
 * jump instantly. FLIP measures where each keyed element was, lets the DOM
 * settle, measures where it is, and inverts the difference back so the browser
 * can animate FROM the old position TO the new one. The result is that the
 * reader can follow one passage physically moving down the list, which is what
 * makes "these two methods disagree about THIS evidence" legible. A crossfade
 * would hide the exact fact the lab teaches.
 *
 * Usage:
 *   const flip = useFlipList();
 *   <ol ref={flip.container} data-flip-key={id}>...</ol>
 * The hook records positions before an update and plays the moves after it,
 * driven by `useLayoutEffect` so no intermediate frame is ever painted.
 *
 * Reduced motion: positions still change (the list is reordered - that is
 * information), but nothing slides.
 */
export function useFlipList<T extends HTMLElement = HTMLOListElement>(
  /** Changing this value triggers a reorder measurement + animation. Key it on
   *  the data that reordered (e.g. the mode + result ids), not on render count. */
  signature: unknown,
  options?: { durationMs?: number; itemSelector?: string; keyAttribute?: string },
): React.RefObject<T> {
  const {
    durationMs = 520,
    itemSelector = "[data-flip-key]",
    keyAttribute = "data-flip-key",
  } = options ?? {};

  const container = useRef<T>(null);
  const positions = useRef<Map<string, DOMRect>>(new Map());
  const reduced = useReducedMotion();
  const first = useRef(true);

  // Capture "First" before the DOM updates.
  useLayoutEffect(() => {
    const element = container.current;
    if (!element) return;
    const next = new Map<string, DOMRect>();
    element.querySelectorAll<HTMLElement>(itemSelector).forEach((item) => {
      const key = item.getAttribute(keyAttribute);
      if (key) next.set(key, item.getBoundingClientRect());
    });
    positions.current = next;
  });

  // Measure "Last", "Invert", and "Play" after the update for this signature.
  useEffect(() => {
    const element = container.current;
    if (!element) return;

    // The very first measurement has nothing to animate FROM. Without this
    // guard, mounting the lab would slide every row up from wherever the browser
    // happened to be - a fake entrance that implies movement that did not happen.
    if (first.current) {
      first.current = false;
      return;
    }

    if (reduced || durationMs <= 0) return;

    const items = Array.from(
      element.querySelectorAll<HTMLElement>(itemSelector),
    );
    let animated = 0;

    items.forEach((item) => {
      const key = item.getAttribute(keyAttribute);
      if (!key) return;
      const before = positions.current.get(key);
      if (!before) return; // new item: let it appear, do not invent a journey
      const after = item.getBoundingClientRect();
      const dx = before.left - after.left;
      const dy = before.top - after.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return; // effectively did not move

      animated += 1;
      // A single shared timing curve so the whole list moves as one object.
      // Per-item staggered delays would read as a wave and imply the reorder was
      // a cascade of separate decisions. It was one decision.
      item.animate(
        [
          { transform: `translate(${dx}px, ${dy}px)` },
          { transform: "translate(0px, 0px)" },
        ],
        {
          duration: durationMs,
          easing: "cubic-bezier(0.22, 1, 0.36, 1)",
        },
      );
    });

    // If nothing actually moved (e.g. a mode that agreed with the last one), do
    // nothing - no animation is the honest result, and it is also why the UI must
    // not fake a reorganise to look busy.
    void animated;
  }, [signature, reduced, durationMs, itemSelector, keyAttribute]);

  return container;
}

/* -------------------------------------------------------------------------- */
/* One-shot mount reveal                                                      */
/* -------------------------------------------------------------------------- */

/**
 * True shortly after mount. For staggering children on first paint without
 * involving scroll observation (the hero, empty states).
 */
export function useMounted(delayMs = 30): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setMounted(true), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs]);
  return mounted;
}

/**
 * A stable staggered transition for a list revealed together: each item gets a
 * small incremental delay so a row of evidence reads as arriving in sequence
 * rather than all at once. Capped, because 60 items at 40ms each is two and a
 * half seconds of waiting for the last row.
 */
export function useStagger(total: number, stepMs = 45, maxMs = 420) {
  return useCallback(
    (index: number): { transitionDelay: string } => ({
      transitionDelay: reducedSafeDelay(index, stepMs, maxMs) + "ms",
    }),
    [total, stepMs, maxMs],
  );
}

function reducedSafeDelay(index: number, stepMs: number, maxMs: number): number {
  if (readReducedMotion()) return 0;
  return Math.min(index * stepMs, maxMs);
}

/* -------------------------------------------------------------------------- */
/* Page transition                                                            */
/* -------------------------------------------------------------------------- */

/**
 * Derive the app "section" from a pathname.
 *
 * This is what a page transition keys on. The critical property is that it is
 * the FIRST segment after `/app`, not the whole path: moving between
 * `/app/chat/5` and `/app/chat/6` is the SAME section, so the Chat screen is not
 * remounted and its live stream, scroll position and in-progress question
 * survive. Only a genuine change of screen - chat to learning to analytics -
 * changes the key and re-plays the entrance.
 *
 * That distinction is the whole design of this feature. A page transition built
 * on the full URL would silently kill the streaming answer every time the
 * conversation id updated mid-stream, which is a data bug dressed as an effect.
 */
export function appSection(pathname: string): string {
  const match = /^\/app\/([^/]+)/.exec(pathname);
  if (!match) return pathname === "/app" ? "home" : "";
  return match[1] ?? "";
}
