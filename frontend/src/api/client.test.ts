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

test("getProviderDirectory carries the deployment's own caps", async () => {
  server.use(
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({
        providers: [],
        limits: {
          max_upload_bytes: 104857600,
          max_sync_duration_s: 180,
          max_async_duration_s: 14400,
          max_live_preview_duration_s: 120,
        },
      }),
    ),
  );

  const directory = await client.getProviderDirectory();

  expect(directory.limits.max_upload_bytes).toBe(104857600);
  expect(directory.limits.max_async_duration_s).toBe(14400);
});

test("getEvaluationArtifact returns the stored artifact document", async () => {
  server.use(
    http.get(`${BASE}/v1/evaluations/e-1/artifact`, () =>
      HttpResponse.json({
        dataset: { dataset_id: "stt-eval-core", version: "1.0.0" },
        provider: "faster-whisper",
        model: "large-v3-turbo",
        decoding: { language: "en" },
        normalization: { name: "stt-normalize-v1" },
        clips: [
          {
            clip_id: "syn-en-tech-001",
            status: "scored",
            provider: "faster-whisper",
            model: "large-v3-turbo",
            audio_sha256: "abc",
            transcript_text: "the quick fox",
            reference_text: "the quick brown fox",
            alignment: {
              code: "MMDM",
              alphabet: { equal: "M", substitute: "S", delete: "D", insert: "I" },
              reference_tokens: ["the", "quick", "brown", "fox"],
              hypothesis_tokens: ["the", "quick", "fox"],
            },
            accuracy: {
              wer: 0.25,
              cer: 0.1,
              normalization: { name: "stt-normalize-v1" },
              word_errors: {
                substitutions: 0,
                deletions: 1,
                insertions: 0,
                errors: 1,
                reference_units: 4,
                hypothesis_units: 3,
              },
              char_errors: {
                substitutions: 0,
                deletions: 1,
                insertions: 0,
                errors: 1,
                reference_units: 4,
                hypothesis_units: 3,
              },
            },
            timestamps: null,
            timings: { processing_s: 0.9, real_time_factor: 0.07 },
            attempts: 1,
            retries: 0,
            error: null,
          },
        ],
        aggregate: { wer_micro: 0.25, clips_scored: 1, clips_total: 1 },
        by_group: {},
        insufficient_evidence: true,
        started_at: "2026-10-11T20:00:00",
        finished_at: "2026-10-11T20:00:12",
        duration_s: 12.0,
        mlflow: null,
      }),
    ),
  );

  const artifact = await client.getEvaluationArtifact("e-1");

  expect(artifact.dataset.dataset_id).toBe("stt-eval-core");
  expect(artifact.clips[0].alignment?.code).toBe("MMDM");
  expect(artifact.clips[0].accuracy?.word_errors.deletions).toBe(1);
  expect(artifact.aggregate.wer_micro).toBe(0.25);
});

test("getEvaluationArtifact keeps the honest 404 and 503 as typed errors", async () => {
  server.use(
    http.get(`${BASE}/v1/evaluations/e-1/artifact`, () =>
      HttpResponse.json(
        {
          error: {
            code: "ARTIFACT_NOT_FOUND",
            message: "evaluation 'e-1' stored no artifact",
          },
        },
        { status: 404 },
      ),
    ),
  );

  await expect(client.getEvaluationArtifact("e-1")).rejects.toMatchObject({
    status: 404,
    code: "ARTIFACT_NOT_FOUND",
  });

  server.use(
    http.get(`${BASE}/v1/evaluations/e-1/artifact`, () =>
      HttpResponse.json(
        {
          error: {
            code: "STORAGE_NOT_CONFIGURED",
            message: "this deployment has no object storage configured",
          },
        },
        { status: 503 },
      ),
    ),
  );

  await expect(client.getEvaluationArtifact("e-1")).rejects.toMatchObject({
    status: 503,
    code: "STORAGE_NOT_CONFIGURED",
  });
});

test("getEvaluationMetrics returns every explanation a metric carries", async () => {
  server.use(
    http.get(`${BASE}/v1/evaluations/metrics`, () =>
      HttpResponse.json({
        metrics: [
          {
            name: "wer_micro",
            applies_to: "run aggregate",
            unit: "ratio — total word errors over total reference words",
            direction: "lower is better",
            formula:
              "sum(errors over scored clips) / sum(reference words over scored clips)",
            inputs: "every clip the run scored",
            normalization: "stt-normalize-v1",
            edge_behaviour: "null when the run scored no clips with a reference",
            limitations: "long clips dominate",
            computed_by: "wer_micro",
          },
          {
            name: "rtf_mean",
            applies_to: "run aggregate",
            unit: "ratio — processing seconds per audio second",
            direction: "lower is better",
            formula: "mean(processing seconds / audio seconds), per clip",
            inputs: "the provider's own timing",
            normalization: "none",
            edge_behaviour: "null for clips the provider timed, none of which it did",
            limitations: "includes model warm-up",
            computed_by: "rtf_mean",
          },
        ],
      }),
    ),
  );

  const list = await client.getEvaluationMetrics();

  expect(list.metrics).toHaveLength(2);
  expect(list.metrics[0].computed_by).toBe("wer_micro");
  expect(list.metrics[0].applies_to).toBe("run aggregate");
  expect(list.metrics[1].formula).toBe("mean(processing seconds / audio seconds), per clip");
});
