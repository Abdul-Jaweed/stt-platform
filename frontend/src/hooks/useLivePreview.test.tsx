import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createApiClient } from "../api/client";
import { server } from "../test/server";
import { useLivePreview } from "./useLivePreview";

const BASE = "http://127.0.0.1:8200";
const client = createApiClient(BASE);

const previewBody = {
  text: "hello from the microphone",
  language: "en",
  segments: [
    {
      start: 0,
      end: 2.5,
      text: "hello from the microphone",
      confidence: null,
      speaker: null,
      words: [],
    },
  ],
  timestamp_granularity: "segment" as const,
  audio_duration_s: 3,
  processing_duration_s: 0.4,
  provider: "faster-whisper",
  model: "large-v3-turbo",
  model_version: null,
  is_final: false as const,
};

function makeBlob(): Blob {
  return new Blob(["fake-webm-bytes"], { type: "audio/webm" });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

test("asks for a preview while recording and surfaces the text", async () => {
  // Real timers: the response has to travel through msw, and fake timers
  // starve that hop.
  vi.useRealTimers();
  let calls = 0;
  server.use(
    http.post(`${BASE}/v1/transcriptions/preview`, () => {
      calls += 1;
      return HttpResponse.json(previewBody);
    }),
  );

  const { result } = renderHook(() =>
    useLivePreview({
      client,
      active: true,
      seconds: 6,
      getBlob: makeBlob,
      intervalMs: 20,
    }),
  );

  await waitFor(() => expect(result.current.status).toBe("live"));
  expect(calls).toBeGreaterThanOrEqual(1);
  expect(result.current.text).toBe("hello from the microphone");
  expect(result.current.provider).toBe("faster-whisper");
});

test("waits until there is something worth transcribing", async () => {
  let calls = 0;
  server.use(
    http.post(`${BASE}/v1/transcriptions/preview`, () => {
      calls += 1;
      return HttpResponse.json(previewBody);
    }),
  );

  const { result } = renderHook(() =>
    useLivePreview({
      client,
      active: true,
      seconds: 0.5,
      getBlob: makeBlob,
      intervalMs: 3000,
      minSeconds: 2,
    }),
  );

  await act(async () => {
    await vi.advanceTimersByTimeAsync(6500);
  });

  expect(calls).toBe(0);
  expect(result.current.status).toBe("waiting");
});

test("a failing preview is reported without disturbing anything else", async () => {
  vi.useRealTimers();
  server.use(
    http.post(`${BASE}/v1/transcriptions/preview`, () =>
      HttpResponse.json(
        { error: { code: "PROVIDER_ERROR", message: "boom" } },
        { status: 502 },
      ),
    ),
  );

  const { result } = renderHook(() =>
    useLivePreview({ client, active: true, seconds: 6, getBlob: makeBlob, intervalMs: 20 }),
  );

  await waitFor(() => expect(result.current.status).toBe("error"));
  expect(result.current.error).toContain("boom");
});

test("an empty blob is not sent anywhere", async () => {
  let calls = 0;
  server.use(
    http.post(`${BASE}/v1/transcriptions/preview`, () => {
      calls += 1;
      return HttpResponse.json(previewBody);
    }),
  );

  renderHook(() =>
    useLivePreview({
      client,
      active: true,
      seconds: 6,
      getBlob: () => null,
      intervalMs: 3000,
    }),
  );

  await act(async () => {
    await vi.advanceTimersByTimeAsync(9500);
  });

  expect(calls).toBe(0);
});

test("stops asking once recording stops", async () => {
  let calls = 0;
  server.use(
    http.post(`${BASE}/v1/transcriptions/preview`, () => {
      calls += 1;
      return HttpResponse.json(previewBody);
    }),
  );

  const { rerender } = renderHook(
    ({ active }: { active: boolean }) =>
      useLivePreview({ client, active, seconds: 6, getBlob: makeBlob, intervalMs: 3000 }),
    { initialProps: { active: true } },
  );

  await act(async () => {
    await vi.advanceTimersByTimeAsync(3100);
  });
  const whileRecording = calls;
  expect(whileRecording).toBeGreaterThanOrEqual(1);

  rerender({ active: false });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(9000);
  });

  expect(calls).toBe(whileRecording);
});

test("stops asking past the preview cap", async () => {
  let calls = 0;
  server.use(
    http.post(`${BASE}/v1/transcriptions/preview`, () => {
      calls += 1;
      return HttpResponse.json(previewBody);
    }),
  );

  const { result } = renderHook(() =>
    useLivePreview({
      client,
      active: true,
      seconds: 200,
      getBlob: makeBlob,
      intervalMs: 3000,
      maxSeconds: 120,
    }),
  );

  await act(async () => {
    await vi.advanceTimersByTimeAsync(6500);
  });

  expect(calls).toBe(0);
  expect(result.current.capped).toBe(true);
});
