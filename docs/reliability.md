# Reliability semantics — Phase 1 (synchronous path)

What the backend actually guarantees today, and what deliberately waits
for Phase 2. Anything not listed here is not implemented.

## Job lifecycle and transition trail

Statuses: `ACCEPTED → VALIDATING → STORED → QUEUED → PROCESSING →
SUCCEEDED` (terminal), plus `FAILED`, `CANCELLED`, `RETRY_WAIT`
(reserved). `PARTIALLY_SUCCEEDED` is deliberately absent until its
semantics are defined.

- Every transition is **persisted** in `job_transitions`
  (`from_status`, `to_status`, `detail`, timestamp) in the same database
  as the job row — the trail is the audit record.
- The transition map in `domain/status.py` is the single source of
  truth; the repository raises `INVALID_TRANSITION` instead of writing an
  illegal edge.
- `attempt_count` increments on every entry into `PROCESSING`;
  `started_at` set once, `completed_at` on any terminal state.

## Validation (two layers)

1. **Upload layer (HTTP)**: `Content-Length` pre-check, then streamed
   copy with a hard byte cap (413 `UPLOAD_TOO_LARGE`); the file content
   is probed with ffprobe before any job exists — bad media is rejected
   as 415/422 with **no job row** (sync-client UX).
2. **Job layer (service)**: the same validation runs again inside
   `VALIDATING`; a failure there marks the job `FAILED` with
   `retryable=false` (defense in depth — the file could change between
   the two moments).

Extensions are never trusted; duration, codec, channels and size come
from the actual media bytes (spec §8).

## Idempotency

- Key = client-supplied `Idempotency-Key` header, unique per `scope`
  (DB unique constraint `uq_jobs_scope_idempotency_key`).
- Fingerprint = `sha256(provider + language + options + audio bytes)`,
  truncated to nothing — full hex digest.
- Replay with identical fingerprint → the original job is returned
  as-is (no new processing, no new trail entries).
- Same key, different fingerprint → 409 `IDEMPOTENCY_CONFLICT`.
- **Phase 2 gap**: the check-then-insert is not race-safe under
  concurrent identical submissions; the unique index is the backstop,
  and Phase 2 adds IntegrityError → replay resolution plus key expiry.

## Failure classes

| Class | Examples | `failure.retryable` | HTTP (when observable synchronously) |
|---|---|---|---|
| Input, permanent | missing/empty/undecodable media, too long, too large | false | 415 / 413 / 422 |
| Provider, transient | decode/engine hiccup inside transcription | true | 502 `PROVIDER_ERROR` (async path only) |
| Contract violation | provider returns transcript breaking the canonical schema | false | 502/500 depending on origin |

Retry is **caller-driven**: `POST /v1/transcriptions/{id}/retry` is
accepted only for `FAILED` + `retryable=true` + audio still present;
`FAILED → QUEUED` then re-enters `PROCESSING` (attempt_count 2, …).
There is no automatic retry loop in Phase 1 — bounded backoff with
jitter arrives with the orchestrator (Phase 2).

## Multi-tenancy / isolation

- Every repository read and write is filtered by `scope`; a job from
  another scope is indistinguishable from a missing job (404).
- Clients **cannot** supply a scope in Phase 1 — it is fixed server-side
  (`"local"`). Auth/tenant binding arrives with identity (later phase);
  the never-trust-the-client rule is already enforced structurally.

## Concurrency and resources

- The sync endpoint runs in the server's worker threadpool; the
  Faster-Whisper engine is constructed once per process (lazy singleton
  in the adapter) and model load happens on first use.
- GPU is a single shared resource in Phase 1 (one in-flight
  transcription per process). Separate concurrency limits per class
  (CPU / GPU / managed) are Phase 2.
- Readiness reflects PostgreSQL only; it does not yet probe CUDA
  (`/v1/providers` exposes provider health for that today).

## Known Phase 1 gaps (honest list)

- `cancel` → 501; cancellation arrives with the async pipeline.
- An **unexpected exception** during processing (e.g. provider bug
  raising a non-domain error) surfaces as 500 and leaves the job in
  `PROCESSING` — no compensating update yet (sweeper in Phase 2).
- No automatic retries, no circuit breakers, no backoff (Phase 2:
  Temporal workflows + outbox).
- Uploads are retained under `.data/audio/` with no lifecycle/retention
  policy; object storage with opaque keys replaces this in Phase 2.
  An idempotent replay leaves an extra copy of the uploaded bytes on
  disk (the original job's file is authoritative).
- `JobRecord` (API response) includes the local `audio_path` — acceptable
  for the single-node Phase 1; Phase 2 replaces it with an opaque
  storage key.
- Sync duration limit is `STT_MAX_SYNC_DURATION_S` (default 180 s);
  longer audio waits for the queued pipeline.

## Secrets and logs

Settings come from environment (`STT_` prefix, `.env` ignored by git);
no credentials in code or API responses. Request logs are uvicorn
access lines only — audio bytes, transcripts and secrets are never
logged; `INTERNAL_ERROR` messages are deliberately opaque.
