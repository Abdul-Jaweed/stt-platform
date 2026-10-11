# Architecture — Provider-Neutral STT Platform

Status: Phase 0 (initial). Sections marked *(planned — Phase N)* record
decisions now and fill in during later phases. Decision log: §14 (ADRs).

## 1. What this is

A standalone, provider-neutral Speech-to-Text platform. Clients submit
audio; the platform transcribes it — locally on GPU or via managed
providers — and returns transcripts with segment-level (optionally
word-level) timestamps on the **original audio timeline**. One stable
versioned HTTP API serves a separate frontend, voice agents, and
programmatic clients.

The backend is headless: it never depends on a browser, frontend runtime,
or UI framework. The provider is an implementation detail behind an
internal contract; clients never see provider SDK objects.

Principles: accuracy-first (evaluation-driven provider choices),
provider neutrality, durable asynchronous processing, timestamp
fidelity, reliability (idempotency, bounded retries, recovery),
privacy-aware routing, observability (OpenTelemetry), measurable
quality (MLflow / WER / CER), incremental delivery.

## 2. System boundaries

```text
Frontend / Voice Agent / API Client
                 |
                 v
        FastAPI headless backend  (api/ -> services/ -> providers/ + persistence/)
                 |
     +-----------+-----------+
     |           |           |
   Jobs       Audio       Provider
   & API     Pipeline      Router
     +-----------+-----------+
                 |
        Orquestration: Temporal workflows + worker pools   (Phase 2)
           /                       \
   Local Faster-Whisper        Managed adapters (Sarvam / Deepgram / ElevenLabs)
           \                       /                          (Phase 4)
        Transcript normalization (canonical schema, original-timeline timestamps)
                 |
        PostgreSQL (job state)  +  RustFS/S3 (audio + artifacts)   (Phase 2)
```

Phase 1 is a modular monolith: API and processing in one deployable, with
clean internal boundaries so workers can be split out in Phase 2. No
microservice fleet without demonstrated need.

## 3. Repository layout (target)

```text
local-stt/
├── backend/                 # headless FastAPI service (uv, Python 3.13)
│   ├── pyproject.toml, uv.lock, .env.example, Dockerfile (Phase 2+)
│   ├── src/stt_platform/    # api/ core/ schemas/ domain/ services/ providers/
│   │                        # audio/ workflows/ workers/ persistence/ storage/
│   │                        # evaluation/ observability/  (dirs appear as phases need them)
│   ├── migrations/          # Alembic
│   └── tests/               # unit/ integration/ gpu/
├── frontend/                # React + TS + Vite, API-only            (Phase 3)
├── infrastructure/          # docker-compose.yml + service configs
├── docs/                    # architecture.md, api.md, provider-capabilities.md,
│                            # reliability.md, evaluation.md, operations.md, phase reports
├── models/                  # model caches on I: drive     — git-ignored
├── .cache/                  # uv / torch / NeMo caches     — git-ignored
├── audio/  output/  results/                              — git-ignored (local artifacts)
└── legacy prototypes        # app.py, benchmark_*.py, test_vad.py, ... — preserved in place (ADR-006)
```

## 4. Runtime and dependency decisions

- **Backend environment:** `backend/.venv`, uv-managed, **Python 3.13** —
  mirrors the proven working Faster-Whisper environment on this machine
  (ADR-002). The existing `.venv-nemo` (NeMo/torch) and course-root `.venv`
  are left untouched; no environment consolidation.
- **Pinned versions** (resolved 2026-10-11 from the verified working env —
  reuse, do not improvise):

| Component | Version |
|---|---|
| Python | 3.13 |
| faster-whisper | 1.2.1 |
| ctranslate2 | 4.8.2 |
| onnxruntime (Silero VAD) | 1.31.0 |
| nvidia-cublas-cu12 | 12.9.2.10 |
| nvidia-cudnn-cu12 | 9.27.0.42 |
| nvidia-cuda-nvrtc-cu12 | 12.9.86 |
| FastAPI / Uvicorn | 0.143.0 / 0.54.0 |
| Pydantic | 2.14.0 |
| PostgreSQL | 16 (Docker, port 25432) |

  SQLAlchemy 2.x, Alembic, psycopg 3, pydantic-settings, httpx, pytest,
  testcontainers[postgres] are added by the backend lockfile during Phase 1.
- **uv is the only Python workflow** (ADR-010): no pip/conda/poetry; the
  lockfile is committed and reproducible.

## 5. GPU and Windows CUDA

RTX 4060 (8 GB), driver 610.62, CUDA compute 8.9. On Windows, ctranslate2
cannot find cuBLAS/cuDNN until their DLL directories are registered —
`os.add_dll_directory` for `site-packages/nvidia/{cublas,cudnn}/bin`
**before** the first `faster_whisper`/`ctranslate2` import (ADR-009). One
bootstrap module owns this; it must be imported first in every process
that touches the model (API process, GPU tests, workers).

## 6. Model caches (I: drive)

| Cache | Location | Notes |
|---|---|---|
| faster-whisper models | `models/faster-whisper` | canonical, explicit `download_root` (ADR-003); holds `large-v3-turbo` + `faster-distil-whisper-large-v3` |
| HF hub cache | `models/huggingface` | `HF_HOME` explicit; parakeet + one duplicate turbo snapshot (see phase-0 report) |
| uv cache | `.cache/uv` | `UV_CACHE_DIR` |
| torch / NeMo | `.cache/torch`, `.cache/nemo` | NeMo env only |

Verified warm behavior (2026-10-11): cached `large-v3-turbo` load **4.7 s**,
10 s clip inference **0.9 s** (cold first-load incl. download: ~169 s —
must never happen in normal operation). The app never uses user-level
`C:\` caches.

## 7. Canonical data model *(planned — Phase 1)*

**Job** — internal ID; tenant/authorization scope; idempotency key +
request fingerprint; original/normalized audio object refs; format,
codec, sample rate, channels, duration; requested/detected language;
requested/actual provider; model id + version; status; created/started/
completed/updated timestamps; attempt count + normalized failure;
configuration metadata.

**Transcript** — job ID; full text; language; ordered segments
(start, end, text, optional confidence); optional word timings; optional
speaker metadata; timestamp granularity (`segment` | `word`); audio
duration; processing duration; provider/model metadata; warnings; schema
version.

Rules: segment and word timing stay distinct; granularity `segment` must
carry no word timings (never claim more than available); every timestamp
refers to the original audio timeline (chunk offsets restored); missing
confidence/diarization/language is never fabricated.

## 8. Provider contract *(planned — Phase 1)*

Operations: transcription submission; sync results or async provider-job
reference; status polling; cancellation (where supported); capability
discovery; health/availability; error normalization.

Capabilities are explicit (languages, auto-detect, word/segment
timestamps, diarization, streaming, batch, max size/duration, formats,
sync vs async, confidence) and distinguish **documented** from
**verified-in-integration-tests** status. Adapters translate native
requests/responses/errors into canonical types; provider SDK objects
never escape the adapter.

Selection is explicit in Phases 1–4; automatic routing only after the
Phase 6 evaluation system provides evidence (language/domain accuracy,
timestamp quality, latency, cost, health, GPU capacity, privacy).
Fallback/routing must respect privacy settings — local-only audio is
never silently sent to a managed provider.

## 9. Reliability model *(planned — Phase 2)*

Lifecycle: `ACCEPTED → VALIDATING → STORED → QUEUED → PROCESSING →
SUCCEEDED`, plus `RETRY_WAIT`, `FAILED`, `CANCELLED`.
`PARTIALLY_SUCCEEDED` is reserved and not emitted until its semantics are
defined. PostgreSQL is the source of truth; Temporal orchestrates
durable stages, retries, polling, recovery; Redis is cache/coordination
only. A transactional outbox prevents committed-but-undispatched jobs.
Bounded exponential backoff with jitter; transient vs permanent failure
classification; provider `Retry-After` honored; at-least-once with
idempotent effects; separate concurrency pools for CPU conversion, GPU
inference, managed calls, evaluation; backpressure/quotas/timeouts/
circuit breakers. No exactly-once promises across external providers.

## 10. Storage *(PG now; S3 Phase 2)*

PostgreSQL: jobs, state transitions, idempotency records, provider
attempts, transcript metadata/results, tenant refs, evaluation metadata,
retention status. RustFS/S3-compatible (Phase 2): original + normalized
audio, transcript JSON, retained raw provider responses, SRT/VTT,
evaluation artifacts. Opaque keys, tenant isolation, no public buckets,
no large blobs in PG, no audio/secrets/full transcripts in logs by
default. Retention/deletion covers DB rows **and** object artifacts.

## 11. API surface (`/v1`)

| Endpoint | Phase | Notes |
|---|---|---|
| `POST /v1/transcriptions` | 1 | multipart; Phase 1 processes short audio inline (201 + job resource incl. failure state); Phase 2 adds 202 + status URL for long/async jobs. `Idempotency-Key` honored from Phase 1. |
| `GET /v1/transcriptions/{job_id}` | 1 | 404 outside tenant scope |
| `GET /v1/transcriptions/{job_id}/result` | 1 | canonical transcript; 409 while not ready/failed |
| `POST /v1/transcriptions/{job_id}/retry` | 1 | re-run failed+retryable job |
| `POST /v1/transcriptions/{job_id}/cancel` | 2 | 501 until durable workflows exist |
| `GET /v1/providers` | 1 | capabilities + health |
| `GET /v1/health/live` / `ready` | 1 | readiness separated from provider outages |
| `GET /v1/evaluations/{id}` | 6 | 501 until evaluation system exists |

Structured error schema + OpenAPI generated from code. Auth is enforced
before submission and on every retrieval; client-supplied tenant IDs are
never trusted. CORS configured explicitly for the frontend origin.

## 12. Observability and evaluation *(planned — Phases 5–6)*

OpenTelemetry across API, workflows, workers, audio processing, provider
calls, persistence, with trace-context propagation across async
boundaries; structured logs with correlation + job IDs; liveness ≠
readiness. Metrics: API latency/errors, upload + audio duration, queue
wait, end-to-end latency, provider latency/errors/throttling, processing
duration + RTF, retries/fallbacks, queue depth, GPU/CPU/memory, storage
errors, cost estimates. MLflow: versioned datasets with human-verified
references; WER/CER (jiwer), timestamp boundary error, latency/RTF,
failure rates, cost per audio-minute; comparisons not decided on single
recordings; regression checks for material changes.

## 13. Infrastructure and ports

All platform services run from `infrastructure/docker-compose.yml` with a
dedicated port block (**2xxxx**) to avoid collisions with the machine's
other stacks (mlfast: 15432/19000…, enterprise-ai-support: 5432/5000).
Data binds to `.data/` on the I: drive.

| Service | Image | Host port(s) | Phase |
|---|---|---|---|
| PostgreSQL | `postgres:16` | 25432 | 1 |
| Redis | `redis:7-alpine` | 26379 | 2 |
| RustFS (S3) | `rustfs/rustfs:1.0.1` | 29000 / 29001 | 2 |
| Temporal (+ UI) | `temporalio/*` | 27233 / 28233 | 2 |
| OTLP collector / Grafana | otel + grafana | 24317, 24318 / 23000 | 5 |
| MLflow | `ghcr.io/mlflow/mlflow` | 25000 | 6 |

App ports: backend dev **8200** (legacy prototype keeps 8000), frontend
dev **3200** (Phase 3).

## 14. Decisions (ADR log)

- **ADR-001 — Repository root:** `I:\voice-agent-course\local-stt`, as
  named by the master specification; new git repository initialized here.
  Parent-level course scripts stay outside; the `IDEA.md` copy in
  `I:\Transcription-Pipeline` is not authoritative.
- **ADR-002 — Backend runtime:** separate uv env, Python 3.13 (mirrors
  the proven Faster-Whisper setup). Existing venvs are not modified or
  consolidated; do not break the working CUDA configuration.
- **ADR-003 — Model cache:** canonical Faster-Whisper cache at
  `models/faster-whisper` via explicit `download_root`; `HF_HOME` pinned
  to `models/huggingface`. Never download into user-level caches.
- **ADR-004 — DB layer:** SQLAlchemy 2.x (sync) + psycopg 3 + Alembic;
  FastAPI handlers sync (threadpool). Async I/O only where a concrete
  need appears (e.g. Temporal SDK in Phase 2).
- **ADR-005 — Infrastructure:** own compose project with the 2xxxx port
  block; do not reuse stopped foreign containers on this machine.
- **ADR-006 — Prototypes preserved:** `app.py`, `benchmark_*.py`,
  `test_vad.py`, `record.py`, `testing.py`, parent `stt_engine.py` etc.
  stay in place; functionality is reimplemented behind the new structure
  without deleting originals.
- **ADR-007 — Test fixtures:** no committed audio. Committed fixtures are
  generated synthetically (ffmpeg tone); real-speech GPU tests use
  `audio/sample.wav` when present and skip otherwise (it is local-only,
  never committed).
- **ADR-008 — Test database:** ephemeral PostgreSQL via testcontainers
  for tests; compose PostgreSQL (25432) for dev/e2e runs.
- **ADR-009 — Windows CUDA bootstrap:** one module registers cuBLAS/cuDNN
  DLL directories before any ctranslate2 import; imported first in every
  model-using process.
- **ADR-010 — Dependency management:** uv exclusively; `backend/uv.lock`
  committed; no pip/conda/poetry in any project workflow.
