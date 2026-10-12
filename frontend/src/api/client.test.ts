import { File as NodeFile } from "node:buffer";
import { http, HttpResponse } from "msw";

import { server } from "../test/server";
import { createApiClient, streamUrlFor } from "./client";
import { ApiError } from "./errors";
import type { JobRecord } from "./types";

const BASE = "http://127.0.0.1:8200";
const client = createApiClient(BASE);

const jobBody: JobRecord = {
  id: "j-1",
  status: "SUCCEEDED",
  attempt_count: 1,
  options: {},
  created_at: "2026-10-11T00:00:00Z",
  updated_at: "2026-10-11T00:00:00Z",
};

// Node's File (undici-compatible): jsdom's File makes MSW's request-body
// buffering hang. The app always receives real browser Files; only tests
// need this.
function makeFile(name = "a.wav"): File {
  return new NodeFile([new Uint8Array([1, 2, 3])], name, {
    type: "audio/wav",
  }) as unknown as File;
}

test("createTranscription posts multipart and returns the 201 job", async () => {
  let seenIdempotency: string | null = null;
  const seen: {
    provider?: FormDataEntryValue | null;
    language?: FormDataEntryValue | null;
    file?: FormDataEntryValue | null;
  } = {};
  server.use(
    http.post(`${BASE}/v1/transcriptions`, async ({ request }) => {
      seenIdempotency = request.headers.get("Idempotency-Key");
      const form = await request.formData();
      seen.provider = form.get("provider");
      seen.language = form.get("language");
      seen.file = form.get("file");
      return HttpResponse.json(jobBody, { status: 201 });
    }),
  );

  const { status, job } = await client.createTranscription({
    file: makeFile(),
    provider: "faster-whisper",
    language: "en",
    idempotencyKey: "k-1",
  });

  expect(status).toBe(201);
  expect(job.id).toBe("j-1");
  expect(seenIdempotency).toBe("k-1");
  expect(seen?.provider).toBe("faster-whisper");
  expect(seen?.language).toBe("en");
  const part = seen?.file;
  // MSW's node interceptor degrades multipart file parts to strings under
  // vitest/jsdom (no filename metadata survives); exact filename/bytes
  // fidelity is proven by the live e2e against the real backend in T7.
  expect(part).not.toBeNull();
});

test("createTranscription returns 202 without treating it as an error", async () => {
  server.use(
    http.post(`${BASE}/v1/transcriptions`, () =>
      HttpResponse.json({ ...jobBody, status: "QUEUED" }, { status: 202 }),
    ),
  );

  const { status, job } = await client.createTranscription({ file: makeFile() });

  expect(status).toBe(202);
  expect(job.status).toBe("QUEUED");
});

test("error body is normalized into ApiError with code and message", async () => {
  server.use(
    http.get(`${BASE}/v1/transcriptions/x`, () =>
      HttpResponse.json(
        { error: { code: "JOB_NOT_FOUND", message: "job not found: 'x'" } },
        { status: 404 },
      ),
    ),
  );

  const failure = await client.getJob("x").catch((err: unknown) => err);

  expect(failure).toBeInstanceOf(ApiError);
  expect(failure).toMatchObject({
    status: 404,
    code: "JOB_NOT_FOUND",
    message: "job not found: 'x'",
  });
});

test("network failure becomes ApiError with code NETWORK_ERROR", async () => {
  server.use(http.get(`${BASE}/v1/transcriptions/x`, () => HttpResponse.error()));

  await expect(client.getJob("x")).rejects.toMatchObject({
    code: "NETWORK_ERROR",
  });
});

test("cancel posts and returns the updated job", async () => {
  server.use(
    http.post(`${BASE}/v1/transcriptions/j-1/cancel`, () =>
      HttpResponse.json({ ...jobBody, status: "CANCELLED" }, { status: 200 }),
    ),
  );

  const job = await client.cancel("j-1");

  expect(job.status).toBe("CANCELLED");
});

test("getProviders unwraps the providers array", async () => {
  server.use(
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({
        providers: [
          {
            id: "faster-whisper",
            name: "Faster-Whisper (local)",
            is_local: true,
            capabilities: {
              languages: ["en", "hi"],
              language_detection: true,
              segment_timestamps: true,
              word_timestamps: true,
              diarization: false,
              streaming: false,
              batch: true,
              async_processing: false,
              confidence_scores: true,
              formats: ["wav"],
            },
            health: { status: "ok", detail: "cuda_devices=1" },
          },
        ],
      }),
    ),
  );

  const providers = await client.getProviders();

  expect(providers).toHaveLength(1);
  expect(providers[0].is_local).toBe(true);
  expect(providers[0].capabilities.word_timestamps).toBe(true);
});

test("getEvaluation surfaces 404 as a typed not-found error", async () => {
  server.use(
    http.get(`${BASE}/v1/evaluations/e-1`, () =>
      HttpResponse.json(
        { error: { code: "NOT_FOUND", message: "unknown route" } },
        { status: 404 },
      ),
    ),
  );

  await expect(client.getEvaluation("e-1")).rejects.toMatchObject({
    status: 404,
    code: "NOT_FOUND",
  });
});

test("the stream URL keeps the API's origin and switches the scheme to ws", () => {
  expect(streamUrlFor("http://127.0.0.1:8200")).toBe(
    "ws://127.0.0.1:8200/v1/transcriptions/stream",
  );
  expect(streamUrlFor("https://stt.example.com/")).toBe(
    "wss://stt.example.com/v1/transcriptions/stream",
  );
  // Behind nginx the base is empty and the console is same-origin.
  expect(streamUrlFor("")).toBe(
    `${window.location.origin.replace(/^http/, "ws")}/v1/transcriptions/stream`,
  );
});
