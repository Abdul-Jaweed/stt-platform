# Reliability semantics (Phase 2: durable asynchronous pipeline)

What the backend actually guarantees today. Anything not listed here is
not implemented.

## Two execution paths, one status machine

Statuses: `ACCEPTED → VALIDATING → STORED → QUEUED → PROCESSING →
SUCCEEDED` (terminal), plus `FAILED`, `CANCELLED`, `RETRY_WAIT`
(terminal until the next attempt), all enforced by the transition map in
`domain/status.py`. `PARTIALLY_SUCCEEDED` is deliberately absent until
its semantics are defined.

- **Synchronous path** (short audio, `create_and_process`): by the time
  POST returns, the job is terminal (`SUCCEEDED`/`FAILED`) — the Phase 1
  contract is unchanged.
- **Asynchronous path** (long audio, `create_async`): the original is
  preserved in object storage, the job is enqueued in a transactional
  outbox, and a Temporal workflow owns execution. POST returns 202 with
  the job `QUEUED`.

Routing (server-side, never client-controlled): with object storage
configured, audio `≤ STT_MAX_SYNC_DURATION_S` (default 180 s) takes the
sync path (201); up to `STT_MAX_ASYNC_DURATION_S` (default 7200 s) takes
the async path (202); beyond that → 422 `AUDIO_TOO_LONG`. Without
storage, the service is sync-only (Phase 1 behaviour).

## Durable dispatch: transactional outbox → Temporal

- Job creation for the async path writes **job row + outbox row in one
  transaction** (`create_with_outbox`); a committed job always eventually
  reaches Temporal, and a crash between commit and dispatch only delays
  the job by one dispatcher tick.
- Outbox rows are claimed with `SELECT … FOR UPDATE SKIP LOCKED`; the
  workflow id is derived from the row (`transcribe.<job_id>.<outbox_id>`)
  so re-dispatch of the same row converges to **exactly-once execution**
  (`WorkflowAlreadyStartedError` is treated as success).
- Two dispatchers share the duty: the worker process polls every
  `STT_DISPATCH_POLL_INTERVAL_S`; the API also nudges dispatch
  best-effort right after commit. Rows whose job is already terminal are
  marked spent without starting anything.
- A failed `start_workflow` leaves the row pending (attempts and
  `last_error` recorded) for the next tick.

## Retries: bounded backoff with jitter, inside the workflow

- Attempts are retried **inside the workflow** (not via Temporal's
  activity RetryPolicy) so every failure is visible in the status trail:
  each attempt that fails transiently writes `PROCESSING → RETRY_WAIT`
  (with the failure record), sleeps the computed backoff, then writes
  `RETRY_WAIT → QUEUED → PROCESSING` for the next attempt.
- Backoff = `base × multiplier^(attempt-1)`, capped, with jitter ratio —
  all from settings (`STT_RETRY_*`); the jitter source is the
  deterministic `workflow.random()` RNG so replays are reproducible.
- Attempts are bounded by `STT_RETRY_MAX_ATTEMPTS`; exhausting them ends
  `FAILED` with `retryable=true` (caller may re-enqueue).
- Failure taxonomy: provider `retryable=True` errors and unexpected
  activity errors are transient; provider `retryable=False`, contract
  violations, and validation failures are permanent.

## Cancellation

- `QUEUED` jobs are cancelled directly (one transaction:
  `QUEUED → CANCELLED`).
- Once a workflow exists (`PROCESSING`/`RETRY_WAIT`), `POST …/cancel`
  issues a Temporal cancellation for the workflow derived from the
  latest outbox row; the **workflow owns the final write** — it records
  `… → CANCELLED` best-effort and then ends `Canceled`. A cancellation
  delivered while an attempt is in flight must win over the retry loop.
- Inline (sync) jobs are terminal when the request returns; cancelling a
  terminal job → 409 `INVALID_TRANSITION`.

## Failure classes (async path)

| Class | Examples | `failure.retryable` | Recovery |
|---|---|---|---|
| Input, permanent | undecodable, too long, too large | false | client fixes input |
| Provider, transient | engine hiccup, OOM inside transcription | true | workflow retries (bounded); then caller `POST …/retry` re-enqueues |
| Contract violation | provider breaks the canonical schema | false | code fix |
| Storage at intake (sync path) | upload to S3 failed, local copy kept | true | caller `POST …/retry` (re-uploads from local copy) |
| Storage at intake (async path) | upload to S3 failed, no local copy | false | resubmit the job |
| Missing/unreadable object at execution | object deleted from storage | false | resubmit |

`POST …/retry` routes by state: async jobs (object key, no local copy)
are **re-enqueued** — `FAILED → QUEUED` plus a **new outbox row** (new
workflow id); sync jobs are reprocessed inline from the local file or a
downloaded copy of the stored original.

## Validation (unchanged two layers)

1. **Upload layer (HTTP)**: `Content-Length` pre-check, streamed copy
   with a hard byte cap (413), ffprobe before any job exists (415/422,
   no job row). The cap applied is the async one when storage is
   available, since the request may route async.
2. **Job layer (service)**: re-validated inside `VALIDATING`; failure →
   `FAILED` `retryable=false`. Presigned jobs (`object_key`) skip local
   probing — the **worker re-validates the stored object** before
   processing; a bad object fails the job there.

## Idempotency

- Key = client `Idempotency-Key` header, unique per `scope`; fingerprint
  = `sha256(provider + language + options + object_key + audio bytes)`.
- Replay with identical fingerprint returns the original job; same key
  with different parameters → 409 `IDEMPOTENCY_CONFLICT`. Both paths
  (sync and async) share this logic.
- Known gap: the check-then-insert is not race-safe under concurrent
  identical submissions; the unique index is the backstop.

## Object storage

- Originals and artifacts live under **opaque, tenant-scoped keys**
  built only in `storage/keys.py` (`audio/<scope>/<job>/original<ext>`,
  `artifacts/<scope>/<job>/transcript.json`); the physical layout is
  never exposed to clients and client-supplied keys are validated
  against the caller's scope before use (`INVALID_OBJECT_KEY`).
- Presigned PUT URLs are **SigV4** (content-type agnostic). SigV2, the
  boto3 default for presigns, signs `Content-Type` into the URL and any
  client sending `Content-Type: audio/wav` gets a 403
  `SignatureDoesNotMatch` — the client pins `s3v4` for exactly this
  reason.
- The transcript artifact is best-effort; the PostgreSQL row is the
  source of truth for results.
- Local working copies are kept for dev-speed retries; retention and
  deletion policy lands with the hardening phase (Phase 7).

## Concurrency and resources

- The worker executes sync activities (ffmpeg, model inference, boto3)
  in a thread pool sized `max(4, STT_GPU_CONCURRENCY)`;
  `max_concurrent_activities=STT_GPU_CONCURRENCY` is what actually gates
  GPU concurrency, so one worker cannot oversubscribe the GPU.
- The sync endpoint still runs inline in the API's thread pool; the
  model engine is a lazy per-process singleton in the adapter.

## Multi-tenancy / isolation

- Every repository read/write is filtered by `scope`; cross-scope jobs
  are indistinguishable from missing (404). Clients cannot supply a
  scope (fixed server-side, `"local"` until the identity phase); object
  keys are scope-prefixed and scope-verified — the never-trust-the-
  client rule is enforced structurally.

## Known gaps (honest list)

- `RETRY_WAIT` is terminal *until the workflow's next attempt*; a worker
  crash between the sleep and the next transition is recovered by
  Temporal replay (the workflow history re-drives it), but a job whose
  workflow never resumes (task queue removed) stays `RETRY_WAIT` — no
  sweeper yet.
- An unexpected exception in the **sync** path still surfaces as 500 and
  can leave `PROCESSING` (Phase 1 gap; the async path converts activity
  errors into retries/FAILED).
- Idempotency keys never expire; the async create has a tiny
  commit-to-dispatch window where the worker could start the workflow
  before the final `QUEUED` transition commits (the workflow's first
  activity tolerates any pre-PROCESSING state; documented, not swept).
- Presigned uploads are not verified to exist before the job is created
  (the worker fails the job if the object is missing — honest but late).
- Retention/deletion of originals and artifacts is Phase 7.
- Circuit breakers for managed providers are not implemented (Phase 4+).

## Secrets and logs

Settings come from environment (`STT_` prefix, `.env` ignored by git);
no credentials in code, logs, or API responses. Audio bytes, transcripts
and secrets are never logged; `INTERNAL_ERROR` messages are deliberately
opaque. Object contents are never logged by the storage layer.
