# Pipeline and reliability

What happens to an upload, what happens when a step fails, and which claims
in this document are *implemented* (with the file that implements it),
*verified* (with the run that proved it) or *proposed* (nothing to point
at yet). Nothing here is aspirational without saying so.

## 1. The pipeline

An upload enters at `POST /v1/transcriptions` (`api/v1/transcriptions.py`).
The route validates the audio with ffprobe (`audio/validation.py`), then
checks the *effective* limits with one primitive,
`audio/limits.py::check_provider_limits` — the deployment's own caps and the
provider's declared ones, whichever is stricter — and refuses **before** any
vendor call, naming the provider it refused (`docs/providers.md` carries
every declared limit and its source).

From there the request takes one of two roads:

* **Synchronous** (uploads shorter than `STT_MAX_SYNC_DURATION_S`, default
  180 s): the audio is transcribed inside the request. The response carries
  the job record and, on the result route, timestamped segments. Simple,
  bounded, and the only path evaluations use.
* **Durable** (longer uploads): the audio goes into object storage under an
  opaque, tenant-scoped key, a `jobs` row is written, and a **transactional
  outbox** row is written in the *same transaction*
  (`persistence/outbox.py`). A dispatcher claims outbox rows with
  `SELECT … FOR UPDATE SKIP LOCKED` and starts the Temporal workflow; the
  workflow id derives from the outbox row, so a re-dispatch cannot start a
  second workflow for the same job.

Inside the worker, the attempt is one workflow (`workflows/transcription.py`)
whose activity (`workers/activities.py`) re-checks the limits, decodes the
audio, calls the selected provider adapter, and writes the transcript as a
result object plus terminal status transitions. Every provider sits behind
one contract (`providers/base.py`): a provider is chosen by id, and its
capabilities come from the adapter itself, never from the caller.

Failure is a state, not an exception: a failed attempt writes a `FAILED`
transition and the workflow retries under policy; a cancelled attempt
checks `workflow.cancellation_reason()` and catches *both*
`asyncio.CancelledError` and `temporalio.exceptions.CancelledError`
(`docs/incident-recovery.md` walks the states).

## 2. What is measured, and where the numbers come from

* **Traces** (OpenTelemetry, on by default; `STT_OTEL_ENABLED=false` turns
  it off): the API's server span, the provider call (`stt.provider.transcribe`),
  workflow and activity spans. Trace context propagates across the
  API→Temporal boundary — **verified**: one trace id appears in both the API
  and worker logs of the same async job
  (`scripts/observability_evidence.py`, 5/5 checks).
* **Metrics** (`observability/metrics.py`, Prometheus): per-process
  counters/histograms; the API and the worker need distinct ports
  (9466/9467). A dead collector is never an error — the tracing contract
  is that an unreachable endpoint degrades telemetry, not transcription
  (pinned by test).
* **Logs**: plain text by default, one redacted JSON object per line with
  `STT_LOG_JSON=true`; secrets are redacted by a log filter
  (`observability/logging.py`) and a test scans the tree for them.
* **Evaluations**: the only accuracy claims in this repo come from
  `POST /v1/evaluations` against human-verified references, with the
  normalization rules recorded in the artifact and every metric explained
  by the same function that computes it (`docs/evaluation.md`).

## 3. Failure modes and what actually happens

| Failure | Behaviour | Where |
| --- | --- | --- |
| A clip/job fails at the provider | `FAILED` transition with the error, retried per policy; the evaluation counts it in `failure_rate` and leaves its metrics `null` | `workers/activities.py`, `evaluation/runner.py` |
| The collector is unreachable | Traces are dropped; the transcription is untouched | pinned by `tests/unit/test_observability_core.py` |
| Postgres is down | `/v1/health/ready` reports `not_ready` (it runs `SELECT 1`); uploads fail loudly | `api/v1/health.py` |
| A worker dies mid-attempt | The attempt is bounded by `STT_ACTIVITY_ATTEMPT_TIMEOUT_S`; Temporal re-delivers the activity | `core/config.py`, `docs/incident-recovery.md` |
| A client disconnects mid-stream | The session ends, the decoder is reaped, no job is created — a stream is a conversation, not a job | `api/v1/streaming.py` |
| A degenerate segment arrives | A loud error — never dropped, never clamped | `streaming/session.py` |
| The same idempotency key is reused with different parameters | `409 IDEMPOTENCY_CONFLICT` | `api/v1/transcriptions.py` |
| A cross-tenant read | `404` — the tenant is server-assigned from the key or session, never trusted from the client | `api/security.py` |

## 4. Capacity and backpressure, stated honestly

* `STT_GPU_CONCURRENCY=1`, `STT_MANAGED_CONCURRENCY=4`: the local model is
  serialised per process by a bounded semaphore.
* Streaming sessions are bounded per process (`STT_STREAM_MAX_SESSIONS`),
  a busy limiter answers `STREAM_BUSY` rather than queueing.
* The synchronous path is capped by `STT_MAX_SYNC_DURATION_S`; the live
  preview by `STT_MAX_LIVE_PREVIEW_DURATION_S`.
* **Proposed, not implemented**: autoscaling, a queue-depth SLO, and
  per-tenant fair scheduling. Today the deployment is a single worker with a
  GPU; the numbers above are the whole capacity story.

## 5. Retention and deletion

`DELETE /v1/transcriptions/{id}` removes the objects first, then the rows —
so a failure leaves orphaned objects (recoverable) rather than rows
pointing at nothing (misleading). `STT_RETENTION_DAYS` (when set) deletes
terminal jobs older than the window on the same rule. Object keys are
opaque and tenant-scoped; no client ever sees a storage layout.

## 6. What a reviewer should re-run

```bash
cd backend && TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1 uv run pytest -q
cd frontend && npx vitest run && npm run build
cd backend && uv run python ../scripts/e2e_phase7.py     # jobs: sync, async, cancel, delete
cd backend && uv run python ../scripts/e2e_phase8.py     # streaming through the console's own WS
cd backend && uv run python ../scripts/observability_evidence.py   # traces, live stack
```

Each script drives the *deployed* stack through its own public surface and
prints a pass count; the numbers they reported the last time they ran are in
the corresponding `docs/phase-*.md` reports.
