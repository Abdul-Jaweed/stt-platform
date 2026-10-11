# STT Platform (local-stt)

Provider-neutral Speech-to-Text platform: a headless FastAPI backend that
turns audio into timestamped transcripts through swappable providers
(local Faster-Whisper, plus managed Sarvam / Deepgram / ElevenLabs Scribe
behind the same contract), plus a separate React + TypeScript frontend.

## Status

- **Phase 0 complete** — repository/environment inspection, architecture
  decisions, Phase 1 plan. See `docs/phase-0-report.md`,
  `docs/architecture.md`.
- **Phase 1 complete** — headless backend + local Faster-Whisper
  baseline: `/v1` API, canonical schemas, PostgreSQL persistence with
  Alembic migrations, job lifecycle with audit trail and idempotency.
  Verified: real HTTP e2e on the RTX 4060 (RTF ≈ 0.08).
  See `docs/phase-1-report.md`, `docs/api.md`.
- **Phase 2 complete** — durable asynchronous pipeline: S3-compatible
  object storage with opaque keys (RustFS), Temporal workflows with a
  transactional outbox, bounded backoff + jitter with a visible
  `RETRY_WAIT` trail, real cancellation, presigned uploads for large
  files, 202 + poll contract above the sync cap, recovery across worker
  restarts. Verified: full test suite + real-stack e2e
  (`scripts/e2e_phase2.py`). See `docs/phase-2-report.md`,
  `docs/reliability.md`.
- **Phase 3 complete** — separate React + TypeScript frontend: upload with
  provider/language selection, polling job page with cancel/retry,
  timestamped transcript with synced playback and TXT/SRT/VTT exports,
  provider capability table. Verified: 44 frontend tests + real-browser
  e2e against the live stack (`results/phase3_e2e/result.json`). See
  `docs/phase-3-report.md`, `frontend/README.md`.
- **Phase 4 complete** — managed providers behind the same contract:
  Deepgram (nova-3) and ElevenLabs Scribe (sync, word-level), Sarvam
  (asynchronous batch: submit → poll → fetch with a persisted
  `provider_attempts` row so a crash can never re-submit), key-gated
  registration, shared HTTP policy (error mapping, size guard, key
  redaction), GPU/managed concurrency split, honest
  `documented` vs `verified` capability sources. Amazon Transcribe
  deferred (AWS setup unverified). **All three managed providers
  live-verified 2026-10-11** with free-tier keys (one 10 s transcription
  each, STT only) — see `docs/live-provider-verification.md`. Verified:
  214 backend tests + 2 GPU + fake-server e2e
  (`results/phase4_e2e/result.json`). See `docs/phase-4-report.md`,
  `docs/providers.md`.

## Repository layout

| Path | Purpose |
|---|---|
| `backend/` | Headless FastAPI service + Temporal worker (uv, Python 3.13) |
| `frontend/` | Independent React + TypeScript + Vite app (consumes only `/v1`) |
| `infrastructure/` | Docker Compose: PostgreSQL, RustFS, Temporal, Temporal UI |
| `scripts/` | e2e drivers (`e2e_phase2.py`, `e2e_phase3_frontend.py`, `e2e_phase4.py`) |
| `docs/` | Architecture, reports, plans |
| `models/` | Model caches on the I: drive — never committed |
| `.cache/` | uv / torch / NeMo caches — never committed |
| `audio/`, `output/`, `results/` | Local artifacts — never committed |
| `app.py`, `benchmark_*.py`, … | Pre-existing prototype scripts, preserved (ADR-006) |

## Prerequisites

Windows 11 · NVIDIA GPU (RTX 4060, driver 610.62) · `uv` ≥ 0.12 ·
FFmpeg/ffprobe · Docker Desktop · Node 22 / npm 11 *(frontend)*.

## Quickstart

Local services (full stack):

```bash
cd infrastructure
docker compose up -d --wait
# PostgreSQL 16 on 127.0.0.1:25432 · RustFS S3 on 127.0.0.1:29000
# Temporal gRPC on 127.0.0.1:7233 · Temporal UI on 127.0.0.1:28080
```

Backend (two processes: API + worker):

```bash
cd backend
uv sync
uv run alembic upgrade head            # apply the schema
uv run uvicorn stt_platform.main:app --host 127.0.0.1 --port 8200   # terminal 1
uv run python -m stt_platform.workers.transcription_worker          # terminal 2
```

Transcribe a file (short audio answers 201 synchronously; long audio
answers 202 `QUEUED` and is processed by the worker):

```bash
curl -s -F "file=@audio/sample.wav" http://127.0.0.1:8200/v1/transcriptions
```

Frontend (separate app; consumes only `/v1` — see `frontend/README.md`):

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173 (API base: VITE_API_BASE_URL)
```

## Documentation

- `docs/architecture.md` — architecture, decisions (ADR log), ports
- `docs/api.md` — `/v1` walkthrough with verified example runs
- `docs/reliability.md` — guarantees (outbox, retries, cancellation)
  and known gaps
- `docs/phase-0-report.md` — inspection report, reuse map, risks
- `docs/phase-1-report.md` — what Phase 1 shipped, evidence, limitations
- `docs/phase-2-report.md` — durable processing: evidence, limitations
- `docs/phase-3-report.md` — frontend: evidence, limitations
- `docs/providers.md` — provider capability matrix, config, fidelity rules
- `docs/live-provider-verification.md` — 2026-10-11 live runs: numbers, defects, evidence
- `docs/phase-4-report.md` — managed providers: evidence, limitations
- `docs/superpowers/plans/` — per-phase implementation plans
