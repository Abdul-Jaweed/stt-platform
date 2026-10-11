import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import { isTerminal } from "../api/types";
import type { JobRecord } from "../api/types";

export interface PollError {
  code: string;
  message: string;
}

export interface JobPollingState {
  job: JobRecord | null;
  error: PollError | null;
  loading: boolean;
  refresh: () => void;
}

function toPollError(err: unknown): PollError {
  if (err instanceof ApiError) {
    return { code: err.code, message: err.message };
  }
  return { code: "UNKNOWN_ERROR", message: String(err) };
}

/**
 * Polls GET /v1/transcriptions/{id} until a terminal status (or an error),
 * then stops. `refresh` re-fetches once on demand (cancel/retry actions).
 */
export function useJobPolling(
  api: ApiClient,
  id: string,
  intervalMs = 2000,
): JobPollingState {
  const [state, setState] = useState<{
    job: JobRecord | null;
    error: PollError | null;
    loading: boolean;
  }>({ job: null, error: null, loading: true });

  const timerRef = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const fetchOnce = useCallback(async () => {
    try {
      const job = await api.getJob(id);
      setState({ job, error: null, loading: false });
      if (isTerminal(job.status)) stop();
    } catch (err) {
      setState((prev) => ({ ...prev, error: toPollError(err), loading: false }));
      stop();
    }
  }, [api, id, stop]);

  useEffect(() => {
    stop();
    setState({ job: null, error: null, loading: true });
    void fetchOnce();
    timerRef.current = window.setInterval(() => {
      void fetchOnce();
    }, intervalMs);
    return stop;
  }, [fetchOnce, intervalMs, stop]);

  const refresh = useCallback(() => {
    void fetchOnce();
  }, [fetchOnce]);

  return { ...state, refresh };
}
