# Phase 2 — Durable Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Long audio and all production processing run through durable Temporal workflows: uploads land in S3-compatible object storage under opaque keys, job creation dispatches via a transactional outbox, workers execute with bounded backoff + jitter and separate concurrency limits, and jobs survive process/worker restarts.

**Architecture:** The FastAPI layer stays thin: `POST /v1/transcriptions` validates + persists (PostgreSQL = source of truth) + writes an outbox row in the same transaction; durations above the sync cap (or when the worker is the only processor) answer **202 + job ID**. A dispatcher drains the outbox into Temporal workflow starts (workflow ID derived from the outbox row → idempotent re-dispatch). A single transcription worker executes activities: download original from S3 → transcribe → persist result + artifacts. Retries are bounded exponential backoff with jitter implemented *in the workflow* so every attempt is visible in `job_transitions` (`RETRY_WAIT`).

**Tech Stack:** Phase 1 stack + `temporalio` (Python SDK), `boto3` (S3), RustFS 1.0.1 (S3-compatible, already proven on this machine), Temporal server `temporalio/auto-setup` + `temporalio/ui` in Docker Compose, MinIO testcontainer for storage integration tests.

**Spec:** `I:\Transcription-Pipeline\IDEA.md` §Durable jobs and reliability, §Storage and data lifecycle, §Audio processing (chunking conditional), §Headless API (202 + presigned uploads); Phase 1 as-built: `docs/phase-1-report.md`, `docs/reliability.md`.

## Global Constraints

- `uv` is the only Python workflow (`uv lock`, `uv sync`, `uv run`, `uv add`); commit `uv.lock`. Never bare `pip install`.
- Keep model weights, caches, temporary audio, application data on the **I:** drive. `.data/`, `.cache/`, `audio/`, `results/` are never committed.
- Never commit audio, model weights, credentials, or caches. Local-dev compose credentials only (production hardening = Phase 7).
- Object storage: **opaque object keys**, tenant-isolation prefix, buckets never public, audio blob never in PostgreSQL.
- Never log audio bytes, transcripts, or secrets; `INTERNAL_ERROR` messages stay opaque.
- Never silently route local-only audio to a managed provider (managed adapters arrive Phase 4; the contract keeps `is_local` + scope).
- Async requests answer `202 Accepted` + job ID; sync short-audio path (Phase 1) stays as-is below the cap.
- Tests must not require paid provider APIs and must not depend on the compose stack when a container works instead (storage tests use MinIO testcontainer; Temporal integration tests **skip** with a reason when 127.0.0.1:7233 is unreachable).
- Never fabricate timestamps/confidence; chunk offsets (when chunking is ever needed) map to the original timeline (`domain/timeline.shift_segments`).
- Do not use bare `localhost` in host URLs (Windows resolves to `::1`) — use `127.0.0.1`. Testcontainers needs `TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1` (already set in `tests/conftest.py`).
- `av==18.1.0` stays pinned (av 19.x breaks faster-whisper 1.2.1 decode).
- Gates for every task: `uv run pytest -q` green; GPU gate (`-m gpu`) must stay green at task commits that touch the provider path.

## Review Focus

The five input classes / failure modes most likely to bite a user of this software, each pinned by a test in the owning task:

1. **Duplicate dispatch after a dispatcher crash** (outbox row not marked, re-drained on restart) — must start the workflow at most once and never duplicate processing (T3, T4).
2. **Transient provider failure that exhausts retries** — trail must show `RETRY_WAIT` between attempts and end `FAILED` with `failure.retryable=true`, never stuck in `PROCESSING` (T4).
3. **Client cancels while the job is still QUEUED (workflow not yet started)** — must land `CANCELLED` deterministically, no orphan workflow that later transcribes a cancelled job (T3/T6).
4. **Corrupt/oversized object referenced by a presigned upload** (client PUTs anything under the key) — worker must re-validate downloaded content before transcribing and fail the job permanently (T5, T7).
5. **Object storage briefly unreachable mid-processing** — activity failure must be treated as transient (retry → `RETRY_WAIT`), not a permanent crash that leaves `PROCESSING` forever (T4, T8).

---

### Task 1: Object storage client (`storage/`) — TDD

**Files:**
- Create: `backend/src/stt_platform/storage/{__init__,keys,s3}.py`
- Modify: `backend/src/stt_platform/core/config.py` (S3 settings)
- Test: `backend/tests/integration/test_storage_s3.py`

**Interfaces (produces):**
- `keys.original_key(scope: str, job_id: str, suffix: str) -> str` → `audio/{scope}/{job_id}/original{suffix}`; `keys.artifact_key(scope, job_id, name) -> str` → `artifacts/{scope}/{job_id}/{name}`; both sanitize `suffix` (alnum/dot only), scope/job_id are uuid-safe.
- `S3ObjectStore(endpoint_url, access_key, secret_key, region, bucket_audio, bucket_artifacts, use_tls=False)` with `put_file(bucket, key, path)`, `put_bytes(bucket, key, data, content_type=None)`, `get_bytes(bucket, key) -> bytes`, `exists(bucket, key) -> bool`, `delete(bucket, key)`, `presign_put(bucket, key, expires_s=900) -> str`.
- Settings additions: `s3_endpoint: str = "http://127.0.0.1:29000"`, `s3_access_key: str = "stt"`, `s3_secret_key: str = "stt123"`, `s3_region: str = "us-east-1"`, `s3_bucket_audio: str = "stt-audio"`, `s3_bucket_artifacts: str = "stt-artifacts"`.
- Dependency: `uv add boto3`.

**Test (integration, MinIO testcontainer):**
```python
# tests/integration/test_storage_s3.py
import pytest
from stt_platform.storage.keys import artifact_key, original_key
from stt_platform.storage.s3 import S3ObjectStore

@pytest.fixture(scope="module")
def minio():
    from testcontainers.minio import MinioContainer
    with MinioContainer() as mc:
        yield mc

@pytest.fixture()
def store(minio):
    url = f"http://{minio.get_container_host_ip()}:{minio.get_exposed_port(9000)}"
    s = S3ObjectStore(
        endpoint_url=url, access_key=minio.access_key, secret_key=minio.secret_key,
        region="us-east-1", bucket_audio="stt-audio", bucket_artifacts="stt-artifacts",
    )
    s.ensure_buckets()
    return s

def test_put_get_delete_roundtrip(store, tmp_path):
    f = tmp_path / "a.bin"; f.write_bytes(b"hello s3")
    key = original_key("local", "job-1", ".wav")
    store.put_file("stt-audio", key, f)
    assert store.exists("stt-audio", key)
    assert store.get_bytes("stt-audio", key) == b"hello s3"
    store.delete("stt-audio", key)
    assert not store.exists("stt-audio", key)

def test_presigned_put_is_usable_with_plain_http(store):
    import httpx
    key = artifact_key("local", "job-2", "note.txt")
    url = store.presign_put("stt-artifacts", key, expires_s=300)
    r = httpx.put(url, content=b"via presign")
    assert r.status_code == 200
    assert store.get_bytes("stt-artifacts", key) == b"via presign"

def test_keys_are_opaque_and_scoped():
    assert original_key("local", "j", ".wav") == "audio/local/j/original.wav"
    assert original_key("local", "j", "../evil") == "audio/local/j/original"
```

- [ ] **Step 1:** write tests above. **Step 2:** `uv run pytest tests/integration/test_storage_s3.py -q` → FAIL (`No module named 'stt_platform.storage'`). **Step 3:** `uv add boto3`; implement `keys.py` + `s3.py` (boto3 client, `ensure_buckets` = create if missing; `presign_put` = `generate_presigned_url("put_object")`; `get_container_host_ip` from conftest override already handled). Add settings. **Step 4:** PASS + full suite. **Step 5:** commit `feat(backend): S3-compatible object storage client with opaque keys (T1)`.

### Task 2: Compose infra — RustFS + Temporal (+UI)

**Files:**
- Modify: `infrastructure/docker-compose.yml`

**Interfaces (produces):** services `rustfs` (host `127.0.0.1:29000` S3 / `29001` console), `storage-init` (creates `stt-audio`, `stt-artifacts`), `temporal-postgres` (internal), `temporal` (gRPC `127.0.0.1:7233`), `temporal-ui` (`127.0.0.1:28080`). RustFS service definition is the proven mlfast pattern: image `rustfs/rustfs:1.0.1@sha256:1803fa…` (already on disk), env `RUSTFS_VOLUMES=/data`, `RUSTFS_ADDRESS=0.0.0.0:9000`, console enabled, `RUSTFS_ACCESS_KEY=stt`, `RUSTFS_SECRET_KEY=stt123`; volume `../.data/rustfs:/data`; healthcheck `curl -f http://127.0.0.1:9000/health`. Bucket init uses `bitnamilegacy/minio-client:2025.7.21-debian-12-r3` (already on disk) with `mc alias set local http://rustfs:9000 stt stt123; mc mb local/stt-audio local/stt-artifacts --ignore-existing`. Temporal: `temporalio/auto-setup` **and** `temporalio/ui` pulled now, then **pinned by digest** in the compose file; `temporal` env: `DB=postgres12`, `DB_PORT=5432`, `POSTGRES_SEEDS=temporal-postgres`, `POSTGRES_USER=temporal`, `POSTGRES_PWD=temporal`, depends_on its postgres healthy; `temporal-postgres` = `postgres:16` with `POSTGRES_DB=temporal`.

**Steps:**
- [ ] **Step 1:** `docker pull temporalio/auto-setup:latest && docker pull temporalio/ui:latest` — then `docker inspect --format='{{index .RepoDigests 0}}'` both → write BOTH as `image: …@sha256:…` pins (reproducible, matches RustFS pattern). Record the resolved versions in the commit message.
- [ ] **Step 2:** extend `infrastructure/docker-compose.yml` with the six services/definitions above.
- [ ] **Step 3:** `cd infrastructure && docker compose up -d --wait` (background mode per environment quirk) → then verify, recording real outputs: `docker compose ps` (all healthy); `curl -s http://127.0.0.1:29000/health`; `uv run python -c "import boto3,os; c=boto3.client('s3',endpoint_url='http://127.0.0.1:29000',aws_access_key_id='stt',aws_secret_access_key='stt123',region_name='us-east-1'); print([b['Name'] for b in c.list_buckets()['Buckets']])"` → `['stt-audio','stt-artifacts']` (plus any RustFS internals); `uv run python -c "from temporalio.client import Client; import asyncio; print(asyncio.run(Client.connect('127.0.0.1:7233')).service_client.workflow_service.__class__.__name__)"` after `uv add temporalio` — proves gRPC up.
- [ ] **Step 4:** re-run `uv run pytest -q` (compose up must not break tests). Commit `feat(infrastructure): rustfs + temporal services, s3 bucket init (T2)` (include `uv.lock` from `uv add temporalio boto3`).

### Task 3: Migration 0002 + transactional outbox + dispatcher — TDD

**Files:**
- Create: `backend/src/stt_platform/persistence/outbox.py`, `backend/src/stt_platform/services/dispatch.py`
- Modify: `backend/src/stt_platform/persistence/models.py` (OutboxRow; `JobRow.original_object_key`), `backend/src/stt_platform/domain/status.py` (RETRY_WAIT → CANCELLED), `backend/src/stt_platform/schemas/jobs.py` (`JobRecord.original_object_key`), new alembic revision `0002_*`
- Test: `backend/tests/unit/test_status_map.py` (extend), `backend/tests/integration/test_outbox_dispatch.py`

**Interfaces (produces):**
- `OutboxRow`: `id` serial pk, `job_id` fk→jobs (cascade), `topic` (`"transcription.start"`), `payload` jsonb, `created_at`, `dispatched_at` nullable, `attempt_count` int, `last_error` text.
- `JobRepository.create_with_outbox(record, *, audio, audio_path) -> JobRecord` — job row **and** outbox row in ONE transaction.
- `OutboxRepository.claim_batch(limit=10) -> list[OutboxRow]` (`SELECT … FOR UPDATE SKIP LOCKED WHERE dispatched_at IS NULL`), `.mark_dispatched(id)`, `.mark_failed(id, error)`.
- `services.dispatch.dispatch_pending(outbox_repo, starter, *, limit=10) -> int` where `starter(payload: dict) -> None` is injected (`TemporalWorkflowStarter` in T4; a fake in tests). Re-dispatch of an already-started workflow must be a no-op: the real starter treats `WorkflowAlreadyStartedError` as success; the dispatcher marks the row dispatched either way. Workflow ID = `f"transcribe.{job_id}.{outbox_id}"`.
- Status map: add `JobStatus.RETRY_WAIT → {QUEUED, FAILED, CANCELLED}`.

**Tests (key cases):**
```python
def test_create_with_outbox_is_one_transaction(repo, ...):      # both rows or neither (force failure → neither)
def test_dispatch_starts_once_and_marks(repo, ...):             # fake starter records calls; rerun → no second call
def test_dispatch_retries_failed_rows(repo, ...):               # starter raises RuntimeError → row keeps dispatched_at NULL, last_error set, attempt_count+1
def test_claim_batch_skips_locked_rows(...):                    # second session can't claim held rows (SKIP LOCKED)
def test_retry_wait_can_be_cancelled(...):                      # status map unit
```

- [ ] **Step 1:** write tests → RED. **Step 2:** implement models + `alembic revision --autogenerate -m "outbox and original object key"` (review generated revision against models, keep it), outbox repo, dispatcher. **Step 3:** GREEN + full suite. **Step 4:** commit `feat(backend): transactional outbox, dispatcher, migration 0002 (T3)`.

### Task 4: Temporal workflows + worker + bounded backoff — TDD

**Files:**
- Create: `backend/src/stt_platform/workflows/{__init__,transcription.py}`, `backend/src/stt_platform/workers/{__init__,transcription_worker.py,retry.py}`, `backend/src/stt_platform/services/temporal_starter.py`
- Modify: `backend/src/stt_platform/core/config.py` (`temporal_host: str = "127.0.0.1:7233"`, `temporal_task_queue: str = "stt-transcription"`, `gpu_concurrency: int = 1`, `retry_max_attempts: int = 3`, `retry_base_delay_s: float = 2.0`, `retry_multiplier: float = 2.0`, `retry_max_delay_s: float = 60.0`, `retry_jitter_ratio: float = 0.25`), `pyproject.toml` (`uv add temporalio`)
- Test: `backend/tests/unit/test_retry_policy.py`, `backend/tests/unit/test_worker_activities.py`, `backend/tests/integration/test_workflow_temporal.py` (skips w/ reason if 7233 unreachable)

**Interfaces (produces):**
- `workers.retry.compute_backoff(attempt: int, *, base: float, multiplier: float, cap: float, jitter_ratio: float, rand: float) -> float` — pure; `attempt` 1-based; `min(cap, base * multiplier**(attempt-1)) * (1 + jitter_ratio*rand)` with `rand ∈ [0,1)` supplied by caller (`workflow.random()` in workflow land).
- Activities (all sync, run in worker): `transition_activity(job_id: str, to_status: str, detail: dict | None = None) -> None`; `execute_attempt_activity(job_id: str, attempt: int) -> dict` — transitions `PROCESSING`, downloads original from S3 to `temp_dir` using `JobRow.original_object_key`, re-validates content (`validate_upload`), calls provider, `save_result`, stores `artifacts/{scope}/{job_id}/transcript.json` + `raw.json`, **returns** `{"outcome": "ok"}` or `{"outcome": "transient"|"permanent", "code": ..., "message": ...}` (never raises for provider-visible failures; unexpected exceptions propagate and are mapped by the workflow to transient with the exception name as the code).
- `TranscriptionWorkflow` (`workflows/transcription.py`): `run(job_id: str)`; loop `attempt = 1..retry_max_attempts`: `execute_attempt_activity(..., retry_policy=RetryPolicy(maximum_attempts=1))` (OUR loop owns retries); on `"ok"` → `transition_activity(SUCCEEDED)` and return; on `"transient"` and attempts left → `transition_activity(RETRY_WAIT, detail)` → `workflow.sleep(compute_backoff(..., rand=workflow.random()))` → `transition_activity(QUEUED)` → continue; on `"permanent"`/exhausted/`ActivityError` → `transition_activity(FAILED, failure-detail)`; `except CancelledError:` → `with workflow.shield(): transition_activity(CANCELLED)` → re-raise.
- `services.temporal_starter.start(payload) -> None` — `Client.connect(temporal_host)`, `start_workflow(TranscriptionWorkflow.run, job_id, id=f"transcribe.{job_id}.{outbox_id}", task_queue=...)`, catch `WorkflowAlreadyStartedError` → return.
- Worker entrypoint: `uv run python -m stt_platform.workers.transcription_worker` — connects, `Worker(..., workflows=[TranscriptionWorkflow], activities=[transition_activity, execute_attempt_activity], max_concurrent_activities=settings.gpu_concurrency)`; also exposes `run_dispatcher_loop()` draining the outbox every 2 s (worker process = dispatcher host).

**Tests:**
```python
# unit
def test_backoff_is_bounded_and_jittered(): ...  # attempt1 base; growth; cap; rand monotonic
def test_execute_attempt_success_writes_trail(...)   # FakeProvider + FakeStore + real PG: PROCESSING → result saved
def test_execute_attempt_transient_returns_transient(...)  # ProviderError retryable
def test_execute_attempt_revalidates_downloaded_bytes(...)  # corrupt object → permanent
# integration (skips when temporal down)
def test_workflow_success_end_to_end(...)          # Client + Worker in-process vs compose Temporal; trail …→SUCCEEDED
def test_workflow_transient_then_success(...)      # flaky provider: RETRY_WAIT visible; attempt_count 2
def test_workflow_permanent_failure(...)           # UNDECODABLE-ish → FAILED retryable=false
def test_workflow_cancel(...)                      # cancel handle → CANCELLED
```

- [ ] **Step 1:** write the 4 unit tests + 4 integration tests → RED. **Step 2:** `uv add temporalio` (if not yet), implement. **Step 3:** unit GREEN; integration GREEN vs compose Temporal (or SKIP with printed reason — record honesty). **Step 4:** full suite + GPU gate; commit `feat(backend): temporal transcription workflow, activities, bounded retries, worker (T4)`.

### Task 5: Storage wiring — originals to S3, artifacts, retry-from-S3 — TDD

**Files:**
- Modify: `backend/src/stt_platform/services/jobs.py` (upload original → S3 after validation; `audio_path` stays the local working copy for sync; artifact write on success; `retry()` downloads from S3 when no local copy), `backend/src/stt_platform/main.py` (wire `S3ObjectStore` into app state), `backend/src/stt_platform/api/deps.py`
- Test: `backend/tests/integration/test_job_service_storage.py` (+ update Phase 1 tests' expectations where they touch `original_object_key`)

**Interfaces (produces):** `JobService(..., store: S3ObjectStore | None = None)`; job stores `original_object_key` when store present; sync success also writes `artifacts/{scope}/{job_id}/transcript.json`; `retry()` source: local `audio_path` if file exists else S3 download to temp; sync path deletes its local upload copy after storing to S3 when `store` is present (file preserved in S3 now).

**Tests (MinIO container + FakeProvider):**
```python
def test_sync_upload_stores_original_and_artifact(...)   # object exists at original_key; transcript.json artifact exists
def test_retry_without_local_file_fetches_from_s3(...)   # delete local file → retry still succeeds
def test_worker_attempt_downloads_from_s3_only(...)      # no local file at all; execute_attempt works
def test_storage_failure_marks_job_failed_transient(...) # store down (dead endpoint) during upload → FAILED retryable=true, not 500
```

- [ ] Steps 1–5 as usual; commit `feat(backend): job originals and artifacts in S3, retry from object storage (T5)`.

### Task 6: Async API path — 202 threshold, real cancel, retry re-enqueue — TDD

**Files:**
- Modify: `backend/src/stt_platform/api/v1/transcriptions.py`, `backend/src/stt_platform/services/jobs.py` (add `create_async(...)`, `cancel(...)`), `schemas/jobs.py` if needed
- Test: `backend/tests/integration/test_api_async.py`

**Interfaces (produces):**
- `POST /v1/transcriptions`: after pre-flight validation, probe duration `d`: `d <= max_sync_duration_s` → Phase 1 sync (201, unchanged); `max_sync_duration_s < d <= max_async_duration_s` (new setting `max_async_duration_s: float = 14400.0`) → **202**, job `QUEUED` + outbox row same transaction (+ best-effort `dispatch_pending` after commit; the worker loop covers crash-before-dispatch); `d > max_async_duration_s` → 422 `AUDIO_TOO_LONG`.
- `POST /v1/transcriptions/{id}/cancel` (replaces the 501): `QUEUED` → `CANCELLED` directly (+ outbox row marked dispatched/skipped); `PROCESSING`/`RETRY_WAIT` → `starter.cancel(workflow_id)` (Temporal cancel signal) and return the job; terminal → 409 `INVALID_TRANSITION`. Cancel of a job whose workflow isn't started yet must still end `CANCELLED` (dispatcher skips dispatched/cancelled rows).
- `POST …/retry` on async FAILED: transitions `FAILED→QUEUED` + NEW outbox row (workflow id changes with the row id — Temporal ID reuse avoided) + best-effort dispatch.

**Tests (fake starter injected into app state; real PG):**
```python
def test_long_audio_returns_202_and_queued(...)     # cap=0.5s wav → 202, status QUEUED, outbox row exists
def test_cancel_queued_job(...)                     # → 200 CANCELLED; dispatcher afterwards does NOT start it
def test_cancel_processing_job_calls_temporal(...)  # fake starter.cancel recorded; status transitions via workflow (simulate CANCELLED write)
def test_cancel_terminal_conflict(...)              # 409
def test_retry_async_failed_enqueues_new_outbox_row(...)
def test_longer_than_async_cap_rejected(...)        # 422 AUDIO_TOO_LONG
```

- [ ] Steps as usual; commit `feat(backend): async 202 path, real cancellation, retry re-enqueue (T6)`.

### Task 7: Presigned uploads for large files — TDD

**Files:**
- Modify: `backend/src/stt_platform/api/v1/transcriptions.py` (new `POST /v1/uploads/presign`), service `create_from_object(...)`
- Test: `backend/tests/integration/test_api_presign.py`

**Interfaces (produces):** `POST /v1/uploads/presign` (JSON `{"filename": "x.wav", "content_type": "audio/wav", "scope": null}`) → 200 `{"upload_url", "object_key", "expires_s": 900, "headers": {...}}`; object key under `audio/<scope>/<uuid>/original<ext>` (bucket `stt-audio`); `POST /v1/transcriptions` gains optional form field `object_key` (mutually exclusive with `file` → 422 `VALIDATION_ERROR` if both/neither): the server streams the object from S3 to a temp copy, validates content, then continues exactly like the multipart path (sync/async by duration). Worker re-validates anyway (T4) — presign can't be trusted.

**Tests (MinIO + fake app wiring):**
```python
def test_presign_then_object_key_transcription(...)  # presigned PUT via httpx → POST with object_key → 201 SUCCEEDED
def test_object_key_missing_object(...)              # → 415 UNSUPPORTED_MEDIA (content validation of the fetched object)
def test_file_and_object_key_rejected(...)           # 422 VALIDATION_ERROR
```

- [ ] Steps as usual; commit `feat(backend): presigned upload endpoint and object_key intake (T7)`.

### Task 8: Real-stack e2e, docs, phase report

**Files:**
- Create: `docs/phase-2-report.md`; Modify: `docs/api.md`, `docs/reliability.md`, `README.md`, `backend/README.md`, plan file (tick boxes)
- Record evidence under `results/phase2_e2e/` (gitignored).

**Steps:**
- [ ] **Step 1:** stack up: `docker compose up -d --wait`; `uv run alembic upgrade head`; `uv run uvicorn stt_platform.main:app --host 127.0.0.1 --port 8200` (bg) + `uv run python -m stt_platform.workers.transcription_worker` (bg).
- [ ] **Step 2 — long-audio async e2e:** with `STT_MAX_SYNC_DURATION_S=4` for the server+worker run: `curl -F file=@audio/sample.wav` (10 s) → expect **202** + `QUEUED`; poll `GET /v1/transcriptions/{id}` until `SUCCEEDED`; `GET …/result` text equals the Phase 1 transcript; verify trail via `docker exec stt-postgres psql -c "select from_status,to_status from job_transitions where job_id='…' order by id"`; verify objects via boto3 (`original.wav` + `transcript.json`).
- [ ] **Step 3 — cancellation:** with the worker STOPPED: POST (202 QUEUED) → `cancel` → 200 `CANCELLED`; start worker → job stays `CANCELLED` (dispatcher skips).
- [ ] **Step 4 — recovery:** with the worker STOPPED: POST → QUEUED; start the worker → job completes (proves outbox→workflow→worker durability across process restarts); then kill the worker mid-flight (during a long job), restart → workflow resumes and completes (Temporal replay) — record the actual observed trail.
- [ ] **Step 5:** gates: `uv run pytest -q` and `uv run pytest -m gpu -q`; record real counts. Write `docs/phase-2-report.md` (per-phase format: files, deps, commands, executed tests, limitations, next phase), update reliability/api docs + READMEs, tick plan boxes. Commit `docs(backend): phase 2 report and run documentation (T8)`.

## Self-review

- **Spec coverage:** object storage + opaque keys + tenant prefix → T1/T5; Temporal orchestration + worker pools → T4; idempotency/outbox/duplicate-dispatch → T3; bounded backoff + jitter + transient/permanent → T4; worker-restart recovery → T4/T8; long-audio async + 202 → T6; presigned uploads for large files → T7; concurrency limits (GPU) → T4 (`max_concurrent_activities`); "preserve the original" → T5; don't store audio in PG → T1/T5; retention/deletion → **Phase 7** (spec assigns hardening); Redis cache → not needed by these paths (spec: "not as the only job record"; deferred until a cache is required — documented in report); chunking → deferred with rationale (faster-whisper processes whole files natively; spec: "use explicit local chunking only when necessary"); managed-provider concurrency class → Phase 4 (no managed adapters yet).
- **Placeholder scan:** no TBDs; every task has concrete tests, commands, expected outcomes; interface names consistent across tasks (`original_object_key`, `transcribe.{job_id}.{outbox_id}`, `execute_attempt_activity`).
- **Type consistency:** `OutboxRow.id` used in workflow ID everywhere; `compute_backoff(attempt, *, base, multiplier, cap, jitter_ratio, rand)` single signature; `JobService(..., store=None)` optional everywhere.
