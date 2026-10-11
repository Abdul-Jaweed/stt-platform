# Backend — STT Platform (headless FastAPI service)

Phase 1 scope: local Faster-Whisper `large-v3-turbo` transcription with
PostgreSQL persistence, canonical transcript schemas, and a versioned
`/v1` API. See `../docs/phase-1-report.md` for status and evidence.

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
docker compose up -d --wait postgres     # PostgreSQL 16 on 127.0.0.1:25432

cd ../backend
uv run alembic upgrade head              # create/upgrade the jobs schema
```

Environment overrides: copy `.env.example` to `.env` (all `STT_*`).

## Run

```bash
uv run uvicorn stt_platform.main:app --host 127.0.0.1 --port 8200
# OpenAPI: http://127.0.0.1:8200/docs
```

Smoke:

```bash
curl -s http://127.0.0.1:8200/v1/health/ready
curl -s -F "file=@../audio/sample.wav" http://127.0.0.1:8200/v1/transcriptions
```

## Tests

```bash
uv run pytest            # unit + integration (97 passed; GPU tests excluded)
uv run pytest -m gpu     # GPU tests: real large-v3-turbo on CUDA (needs ../audio/sample.wav)
```

Integration tests start an ephemeral PostgreSQL via testcontainers
(Docker must run). GPU tests are skipped when no CUDA device or sample
is present.
