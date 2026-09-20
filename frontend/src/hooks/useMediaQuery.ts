import { useEffect, useState } from "react";

/**
 * Media query hook.
 *
 * Used for layout decisions that CSS alone cannot make - specifically whether the
 * sidebar is a fixed rail or an overlay drawer, which changes the DOM structure
 * (focus trapping, aria-hidden) and not just the styling.
 *
 * `useSyncExternalStore` would be the modern choice here, but a plain effect is
 * easier to read and this runs at most a handful of times per session.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mediaQuery = window.matchMedia(query);
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches);
    setMatches(mediaQuery.matches);
    mediaQuery.addEventListener("change", onChange);
    return () => mediaQuery.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

/** True below Tailwind's `lg` breakpoint (1024px). */
export function useIsCompact(): boolean {
  return useMediaQuery("(max-width: 1023px)");
}
