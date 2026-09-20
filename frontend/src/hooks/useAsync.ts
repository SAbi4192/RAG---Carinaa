import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";

/**
 * Run an async function and expose loading / error / data.
 *
 * Every screen in this app does the same three things: show a skeleton while
 * loading, show an error with a retry when it fails, and show content when it
 * succeeds. Centralising that means the states cannot be forgotten on one page,
 * and it means error handling is consistent - in particular, an expired session
 * never renders as a red error box, because the auth layer is already redirecting.
 *
 * `deps` controls re-fetching, exactly like `useEffect`. Pass `null` to skip the
 * request entirely (used when a required id is not available yet).
 */

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string;
  /** Re-run the fetcher. */
  reload: () => void;
  /** Optimistically replace the data without a round trip. */
  setData: (next: T | null) => void;
}

export function useAsync<T>(
  fetcher: (() => Promise<T>) | null,
  deps: unknown[] = [],
  options: { skipErrorCodes?: string[] } = {},
): AsyncState<T> {
  const { skipErrorCodes = ["auth_error", "unauthorized"] } = options;

  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(fetcher !== null);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);

  // Guards against a slow response from a stale request overwriting a newer one.
  const requestId = useRef(0);

  useEffect(() => {
    if (!fetcher) {
      setLoading(false);
      return;
    }

    const id = ++requestId.current;
    let cancelled = false;

    setLoading(true);
    setError("");

    fetcher()
      .then((result) => {
        if (cancelled || id !== requestId.current) return;
        setData(result);
      })
      .catch((cause: unknown) => {
        if (cancelled || id !== requestId.current) return;

        // An expired session is not a page-level error: the auth layer will send
        // the user to sign in. Showing "could not load" alongside that would be
        // confusing, so we stay quiet.
        if (cause instanceof ApiError && skipErrorCodes.includes(cause.code)) {
          return;
        }

        setError(
          cause instanceof ApiError
            ? cause.message
            : "Something went wrong while loading this data.",
        );
      })
      .finally(() => {
        if (cancelled || id !== requestId.current) return;
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return { data, loading, error, reload, setData };
}
