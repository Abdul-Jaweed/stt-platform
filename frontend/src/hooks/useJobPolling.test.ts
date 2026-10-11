import { act, renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import { createApiClient } from "../api/client";
import type { JobRecord, JobStatus } from "../api/types";
import { server } from "../test/server";
import { useJobPolling } from "./useJobPolling";

const BASE = "http://127.0.0.1:8200";

function job(status: JobStatus): JobRecord {
  return {
    id: "j-1",
    status,
    attempt_count: 0,
    options: {},
    created_at: "2026-10-11T00:00:00Z",
    updated_at: "2026-10-11T00:00:00Z",
  };
}

afterEach(() => {
  vi.useRealTimers();
});

test("polls until a terminal status and then stops", async () => {
  vi.useFakeTimers();
  let calls = 0;
  server.use(
    http.get(`${BASE}/v1/transcriptions/j-1`, () => {
      calls += 1;
      return HttpResponse.json(job(calls >= 3 ? "SUCCEEDED" : "PROCESSING"));
    }),
  );

  const api = createApiClient(BASE);
  const { result } = renderHook(() => useJobPolling(api, "j-1", 1000));

  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.job?.status).toBe("PROCESSING");

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(result.current.job?.status).toBe("PROCESSING");

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(result.current.job?.status).toBe("SUCCEEDED");

  const callsAtTerminal = calls;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(calls).toBe(callsAtTerminal);
});

test("a 404 surfaces as an error and stops polling", async () => {
  vi.useFakeTimers();
  let calls = 0;
  server.use(
    http.get(`${BASE}/v1/transcriptions/missing`, () => {
      calls += 1;
      return HttpResponse.json(
        { error: { code: "JOB_NOT_FOUND", message: "job not found: 'missing'" } },
        { status: 404 },
      );
    }),
  );

  const api = createApiClient(BASE);
  const { result } = renderHook(() => useJobPolling(api, "missing", 1000));

  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.error?.code).toBe("JOB_NOT_FOUND");
  expect(result.current.loading).toBe(false);

  const callsAfterError = calls;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(calls).toBe(callsAfterError);
});

test("refresh re-fetches once even after polling stopped", async () => {
  vi.useFakeTimers();
  let calls = 0;
  server.use(
    http.get(`${BASE}/v1/transcriptions/j-2`, () => {
      calls += 1;
      return HttpResponse.json({ ...job("SUCCEEDED"), id: "j-2" });
    }),
  );

  const api = createApiClient(BASE);
  const { result } = renderHook(() => useJobPolling(api, "j-2", 1000));

  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(calls).toBe(1);

  await act(async () => {
    result.current.refresh();
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(calls).toBe(2);
});
