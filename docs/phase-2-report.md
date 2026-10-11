# Phase 2 report — Durable processing

**Status:** complete (2026-10-11). Long audio and all production
processing run through durable Temporal workflows; every claim below is
backed by an executed command or a committed test.

**Headline evidence:** full suite `144 passed` (+ 2 GPU) and a real-stack
e2e (`scripts/e2e_phase2.py`, exit 0) where a 200 s wav went
multipart-upload → **202 QUEUED** → worker → **SUCCEEDED** in 18.1 s
wall (RTF ≈ 0.059, ≈ 17× realtime, 287 words), a presigned-upload job
succeeded (342 words), a queued job cancelled cleanly, and a job
survived a full worker restart (held `QUEUED` while the worker was down,
`SUCCEEDED` after restart). Raw evidence:
`results/phase2_e2e/result.json` (local artifact, gitignored).

## What shipped

| Task | Commit | Summary |
|---|---|---|
| T1 | `970b6d0` | S3-compatible object storage client (`storage/{keys,s3}.py`), opaque tenant-scoped keys, RustFS testcontainer |
| T2 | *(infra)* | compose: RustFS 1.0.1 (digest-pinned), storage-init buckets, Temporal 1.29.1 (digest-pinned) + UI |
| T3 | `ba07057` | transactional outbox + dispatcher (SKIP LOCKED claims, workflow id = `transcribe.<job>.<outbox_id>`), migration 0002, `STORED→FAILED` |
| T4 | `d134c76` | Temporal workflow + activities + bounded backoff w/ jitter (`RETRY_WAIT` visible), worker entrypoint, sync bridge |
| T5 | `2ff28dd` | originals preserved in S3 on both paths, transcript artifacts, retry from object storage, storage-outage taxonomy |
| T6 | `e506204` | async API path: 202 routing above the sync cap, real cancel (direct while QUEUED, Temporal once started), retry re-enqueue, best-effort dispatch |
| T7 | `1020db9` | presigned uploads (`/v1/transcriptions/uploads/presign`) + `object_key` intake, SigV4 presign fix |
| fix | `1feb099` | worker single-start (`async with` starts the worker; `worker.run()` double-started the bridge and panicked), main-loop dispatcher bridge, degenerate whisper word timestamps dropped |
| T8 | *(this commit)* | real-stack e2e driver, this report, api/reliability/README docs, plan ticked |

## Files

**New (backend):** `storage/{__init__,keys,s3}.py`,
`persistence/outbox.py`, `services/{dispatch,temporal_starter}.py`,
`workers/{__init__,retry,activities,transcription_worker}.py`,
`workflows/{__init__,transcription}.py`,
`migrations/versions/1aa61d61c6a4_outbox_and_original_object_key.py`.

**Modified (backend):** `core/config.py` (S3/Temporal/retry/presign
settings), `domain/{status,errors}.py` (`STORED→FAILED`, `RETRY_WAIT→
{CANCELLED}`, `StorageNotConfigured`, `InvalidObjectKey`,
`InvalidRequest`), `persistence/{models,repositories}.py`,
`schemas/jobs.py` (`original_object_key`, presign models),
`services/jobs.py` (`create_async`, `cancel`, `retry_async`),
`api/{deps,errors}.py`, `api/v1/transcriptions.py`,
`main.py`, `audio/validation.py` (`max_duration_s` override),
`providers/faster_whisper.py` (degenerate-word filter).

**New (tests):** `rustfs_container.py`, `unit/{test_status_map,
test_retry_policy}.py`, `integration/{test_storage_s3,
test_outbox_dispatch,harness,test_worker_activities,
test_workflow_temporal,test_job_service_storage,test_api_async,
test_presign_upload}.py`; `scripts/e2e_phase2.py`.

**Infra:** `infrastructure/docker-compose.yml` (postgres, rustfs,
storage-init, temporal-postgres, temporal, temporal-ui).

**Deps added:** `boto3`, `temporalio` (uv.lock committed).

## Commands (all executed)

```bash
uv run pytest -q                          # 144 passed, 2 deselected
uv run pytest -m gpu -q                   # 2 passed (real large-v3-turbo on CUDA)
uv run alembic upgrade head               # migration 0002 applied to dev DB
cd infrastructure && docker compose up -d --wait   # 5 services healthy
cd backend && uv run python ../scripts/e2e_phase2.py   # exit 0
```

## Real bugs found by the e2e (and fixed)

1. **Worker crashed at startup:** `pyo3_runtime.PanicException: must
   only be set once: TaskLocals` — `async with Worker(...)` *starts* the
   worker; the entrypoint also called `worker.run()` inside the block,
   double-starting the SDK bridge. Fixed: keep the block alive with an
   event wait; the dispatcher thread now starts workflows through the
   **main loop's** client (one bridge loop per process). The T4 tests
   never hit this because they never call `run()`.
2. **Degenerate word timestamps:** on real 200 s audio whisper emitted
   zero-length/inverted words; the strict schema (`end > start`) failed
   the whole job as a contract violation. Fixed in the adapter: drop the
   unsupported timestamps (never clamp or invent); segment timestamps
   still fail loudly. Regression test added.
3. **Presigned PUT 403 `SignatureDoesNotMatch`:** boto3 default SigV2
   presigns sign `Content-Type` into the URL, so any client sending
   `Content-Type: audio/wav` was rejected. Fixed: pin `signature_version
   ="s3v4"` (content-type agnostic).

## E2E evidence (`results/phase2_e2e/result.json`)

- audio: 200.0 s, 6,400,078 B (15× loop of `audio/sample.wav`)
- **job A** (multipart): 202 → `QUEUED`; trail
  `ACCEPTED→VALIDATING→STORED→QUEUED→PROCESSING→SUCCEEDED`; 18.1 s wall;
  RTF ≈ 0.059 (≈17× realtime); 44 segments / 287 words, word-level,
  `faster-whisper` / `large-v3-turbo`
- **job B** (presigned, `Content-Type: audio/wav`): 202 → `SUCCEEDED`,
  342 words, key `audio/local/<uuid>/original.wav`
- **job C** (cancel while queued): 200 `CANCELLED`; trail ends
  `QUEUED→CANCELLED`
- **job D** (recovery): worker killed → job observed `QUEUED` on six
  consecutive polls over ~12 s → worker restarted → `SUCCEEDED`

## Review-focus coverage (from the plan)

1. duplicate dispatch after a dispatcher crash → T3 dispatcher tests +
   workflow-id derivation (re-dispatch is a no-op).
2. transient failure exhausting retries → `RETRY_WAIT` visible in the
   workflow test trail, ends `FAILED retryable=true`.
3. cancel while QUEUED → deterministic `CANCELLED` (unit + e2e job C).
4. corrupt/oversized presigned object → worker re-validates downloaded
   bytes; permanent failure (T4 activity test).
5. storage unreachable mid-processing → activity failure maps to
   transient → `RETRY_WAIT` retries (T4); at intake it fails the job
   `FAILED` (retryable on the sync path, non-retryable when no local
   copy exists — documented in `docs/reliability.md`).

## Deviations from the plan (deliberate, documented)

- Presign endpoint is `POST /v1/transcriptions/uploads/presign` (nested
  under the resource) instead of `/v1/uploads/presign`.
- Presigned `object_key` intake does not re-validate in the API (no
  local bytes); the worker validates the stored object instead — the
  plan's "server streams the object back" step would add an extra copy
  with no correctness gain.
- Local working copies are **kept** on the sync path (plan said delete);
  retention/deletion is Phase 7 per the spec, and the copy is the retry
  source of last resort.
- `STORED→FAILED` was added to the transition map (storage outage after
  validation) — caught by a witnessed test failure, not foreseen.

## Limitations (honest list)

See `docs/reliability.md` §Known gaps. Highlights: no sweeper for jobs
whose workflow never resumes; idempotency keys never expire; presigned
objects are not verified before job creation (worker fails the job
late); retention/deletion pending Phase 7; circuit breakers for managed
providers arrive with the adapters (Phase 4+).

## Next phase

**Phase 3 — frontend:** React + TypeScript + Vite app consuming only
`/v1` (upload, job polling, transcript viewer with word timestamps,
cancel/retry affordances). The backend contract is frozen at the URL
level: nothing in Phase 3 should require API changes.
