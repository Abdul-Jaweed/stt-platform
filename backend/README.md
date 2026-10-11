# Backend — STT Platform (headless FastAPI service)

Phase 1 scope: local Faster-Whisper `large-v3-turbo` transcription with
PostgreSQL persistence, canonical transcript schemas, and a versioned API.

## Setup (uv only)

```bash
export UV_CACHE_DIR=I:/voice-agent-course/local-stt/.cache/uv   # reuse I: drive wheel cache
cd backend
uv lock
uv sync                 # creates backend/.venv on Python 3.13
```

## Local services

```bash
cd ../infrastructure
docker compose up -d postgres     # PostgreSQL 16 on 127.0.0.1:25432
```

## Run

```bash
cd backend
uv run uvicorn stt_platform.main:app --port 8200
```

## Tests

```bash
uv run pytest            # unit + integration (GPU tests excluded)
uv run pytest -m gpu     # GPU tests: real large-v3-turbo on CUDA (needs audio/sample.wav)
```
