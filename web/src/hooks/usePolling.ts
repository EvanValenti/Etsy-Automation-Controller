import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

interface PollState<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
  /** True once the first successful fetch has landed — lets a component
   * distinguish "no data yet" (initial spinner) from "data present,
   * silently refreshing in the background" (no spinner flash on poll). */
  hasLoadedOnce: boolean;
  /** True while a fetch is in flight AFTER the first load — drives a
   * small "updating…" indicator instead of the full loading state. */
  refreshing: boolean;
  /** Wall-clock time of the last successful fetch, or null before the
   * first one lands. Rendered as "updated Xs ago" in Panel headers. */
  lastUpdated: number | null;
}

/**
 * Polls `fetchFn` every `intervalMs` and exposes the latest result.
 *
 * There is no WebSocket push yet (core/execution/coordinator.py's module
 * docstring documents this as a deliberate V1 gap — see /events in
 * api/main.py, still `NotImplementedError`), so polling is the only way
 * this UI can reflect state changes. Pauses while the tab is hidden to
 * avoid hammering the API from background tabs, and (Step 11) fetches
 * immediately the moment the tab becomes visible again rather than
 * waiting up to `intervalMs` for the next tick — the visible gap between
 * "I switched back to this tab" and "data refreshed" is what "smoother
 * refresh behavior" meant in practice.
 */
export function usePolling<T>(
  fetchFn: () => Promise<T>,
  intervalMs: number,
  deps: unknown[] = [],
): PollState<T> & { refetch: () => void } {
  const [state, setState] = useState<PollState<T>>({
    data: null,
    error: null,
    loading: true,
    hasLoadedOnce: false,
    refreshing: false,
    lastUpdated: null,
  });
  const fetchFnRef = useRef(fetchFn);
  fetchFnRef.current = fetchFn;

  const runFetch = useCallback(async () => {
    setState((prev) => ({ ...prev, refreshing: prev.hasLoadedOnce }));
    try {
      const data = await fetchFnRef.current();
      setState({ data, error: null, loading: false, hasLoadedOnce: true, refreshing: false, lastUpdated: Date.now() });
    } catch (err) {
      setState((prev) => ({ ...prev, error: err as Error, loading: false, refreshing: false }));
    }
  }, []);

  useEffect(() => {
    setState((prev) => ({ ...prev, loading: !prev.hasLoadedOnce }));
    runFetch();

    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") runFetch();
    }, intervalMs);

    function onVisibilityChange() {
      if (document.visibilityState === "visible") runFetch();
    }
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, runFetch, ...deps]);

  return { ...state, refetch: runFetch };
}
