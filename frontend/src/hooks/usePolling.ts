import { useEffect, useState } from "react";

/**
 * Poll an async function on an interval.
 *
 * Used for the ingestion progress fallback: if the SSE stream drops we poll the
 * progress endpoint instead, so the user still sees real state rather than a
 * frozen bar. The interval is cleared on unmount and whenever the key changes.
 *
 * `enabled` exists so callers can stop polling once a document reaches a terminal
 * state - polling forever would be a quiet resource leak.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  options: { enabled?: boolean; onData?: (data: T) => void; onError?: (error: unknown) => void } = {},
): { data: T | null; error: unknown } {
  const { enabled = true, onData, onError } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    let timer: number | undefined;

    const tick = async () => {
      try {
        const result = await fetcher();
        if (cancelled) return;
        setData(result);
        setError(null);
        onData?.(result);
      } catch (cause) {
        if (cancelled) return;
        setError(cause);
        onError?.(cause);
      } finally {
        if (!cancelled) timer = window.setTimeout(tick, intervalMs);
      }
    };

    void tick();

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
    // `fetcher` is intentionally excluded: callers pass an inline closure, and
    // depending on it would restart the poll on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs]);

  return { data, error };
}
