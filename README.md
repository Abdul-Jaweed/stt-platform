# STT Platform (local-stt)

Provider-neutral Speech-to-Text platform: a headless FastAPI backend that
turns audio into timestamped transcripts through swappable providers
(local Faster-Whisper first; Sarvam, Deepgram, ElevenLabs in later
phases), plus a separate React + TypeScript frontend.

## Status

- **Phase 0 complete** — repository/environment inspection, architecture
  decisions, Phase 1 plan. See `docs/phase-0-report.md`,
  `docs/architecture.md`.
- **Phase 1 in progress** — headless backend + local Faster-Whisper
  baseline.

## Repository layout

| Path | Purpose |
|---|---|
| `backend/` | Headless FastAPI service (uv, Python 3.13) |
| `frontend/` | Independent React + TypeScript + Vite app *(Phase 3)* |
| `infrastructure/` | Docker Compose for local services (PostgreSQL, …) |
| `docs/` | Architecture, reports, plans |
| `models/` | Model caches on the I: drive — never committed |
| `.cache/` | uv / torch / NeMo caches — never committed |
| `audio/`, `output/`, `results/` | Local artifacts — never committed |
| `app.py`, `benchmark_*.py`, … | Pre-existing prototype scripts, preserved (ADR-006) |

## Prerequisites

Windows 11 · NVIDIA GPU (RTX 4060, driver 610.62) · `uv` ≥ 0.12 ·
FFmpeg/ffprobe · Docker Desktop · Node 22 / npm 11 *(frontend)*.

## Quickstart

Local services:

```bash
cd infrastructure
docker compose up -d postgres        # PostgreSQL 16 on 127.0.0.1:25432
```

Backend (available once Phase 1 lands):

```bash
cd backend
uv sync
uv run uvicorn stt_platform.main:app --port 8200
```

## Documentation

- `docs/architecture.md` — architecture, decisions (ADR log), ports
- `docs/phase-0-report.md` — inspection report, reuse map, risks
- `docs/superpowers/plans/` — per-phase implementation plans
