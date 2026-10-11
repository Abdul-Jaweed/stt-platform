# Backend — STT Platform (headless FastAPI service + Temporal worker)

Local Faster-Whisper `large-v3-turbo` transcription with PostgreSQL
persistence, canonical transcript schemas, a versioned `/v1` API, and
(Phase 2) a durable asynchronous pipeline: object storage with opaque
keys, transactional-outbox dispatch into Temporal workflows, bounded
retries with a visible trail, cancellation, and presigned uploads.
See `../docs/phase-2-report.md` and `../docs/reliability.md`.

## Setup (uv only)

```bash
export UV_CACHE_DIR=I:/voice-agent-course/local-stt/.cache/uv   # reuse I: drive wheel cache
cd backend
uv lock
uv sync                 # creates backend/.venv on Python 3.13
```

## Local services + migrations

```bash
cd ../infrastructure
docker compose up -d --wait              # PostgreSQL, RustFS, Temporal, UI

cd ../backend
uv run alembic upgrade head              # create/upgrade the schema
```

Environment overrides: copy `.env.example` to `.env` (all `STT_*`).

## Run (two processes)

```bash
# API
uv run uvicorn stt_platform.main:app --host 127.0.0.1 --port 8200
# OpenAPI: http://127.0.0.1:8200/docs

# Worker (activities + outbox dispatcher; owns GPU concurrency)
uv run python -m stt_platform.workers.transcription_worker
```

Smoke:

```bash
curl -s http://127.0.0.1:8200/v1/health/ready
curl -s -F "file=@../audio/sample.wav" http://127.0.0.1:8200/v1/transcriptions
```

Short audio (≤ `STT_MAX_SYNC_DURATION_S`, default 180 s) answers **201**
with a terminal job; longer audio answers **202** + `QUEUED` and is
processed by the worker. Watch jobs in Temporal UI:
http://127.0.0.1:28080

## Tests

```bash
uv run pytest            # unit + integration (storage via RustFS testcontainer;
                         # Temporal workflow tests skip if 127.0.0.1:7233 is down)
uv run pytest -m gpu     # GPU tests: real large-v3-turbo on CUDA (needs ../audio/sample.wav)
```

Integration tests start ephemeral PostgreSQL via testcontainers and an
ephemeral RustFS container (Docker must run). GPU tests are skipped when
no CUDA device or sample is present.

## End-to-end

```bash
# with the compose stack up:
uv run python ../scripts/e2e_phase2.py   # 202 → worker → SUCCEEDED, presign,
                                         # cancel, worker-restart recovery
```
