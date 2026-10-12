# STT Platform

**Provider-neutral speech-to-text platform.** One `/v1` API turns audio into
timestamped transcripts through swappable engines — a local Whisper model on
CUDA or a hosted provider — behind a single internal contract, with durable
job handling and an evaluation system that shows the arithmetic behind every
number it reports.

[![Docs](https://github.com/Abdul-Jaweed/stt-platform/actions/workflows/docs.yml/badge.svg?branch=main)](https://github.com/Abdul-Jaweed/stt-platform/actions/workflows/docs.yml)
[![Documentation](https://img.shields.io/badge/docs-live-7c3aed)](https://abdul-jaweed.github.io/stt-platform/)
![Python 3.13](https://img.shields.io/badge/python-3.13-3776ab?logo=python&logoColor=white)
![Docker Compose](https://img.shields.io/badge/docker-compose-2496ed?logo=docker&logoColor=white)

![Timestamped transcript with word-level timings](docs/images/ui-transcript.png)

---

## Overview

Speech-to-text is easy to demo and hard to operate honestly. Providers differ
in what they support, disagree about limits, and return timestamps you cannot
judge without ground truth. STT Platform is a self-hosted platform that treats
those differences as first-class facts:

- **One API, swappable engines.** The same request works against a local
  Faster-Whisper model and against Deepgram, ElevenLabs Scribe and Sarvam —
  each behind one internal contract, each declaring its own capabilities.
- **Claims carry provenance.** A capability is marked `documented` until this
  repository's own live run proves it, then `verified`. Limits are enforced
  *before* the vendor call, and a refusal names the provider that would have
  refused you.
- **Jobs you can trust.** Long audio becomes a durable Temporal workflow with
  a transactional outbox, visible state transitions, retries you can watch,
  and cancel/retry/delete that actually mean something.
- **Honest measurement.** The evaluation system scores only against
  human-verified references, never invents a number for something it could
  not measure (a metric that could not be computed is `null`, never `0.0`),
  and explains every metric with the same function that computes it.
- **Privacy is structural.** Audio lives under opaque, tenant-scoped object
  keys; local-only audio is never silently routed to a cloud provider; audio
  and transcripts never reach the logs or traces.

Built for teams embedding voice into their own products, for self-hosters who
need audio to stay on their machines, and for anyone who wants transcription
numbers they can defend.

## Table of Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Design Decisions](#design-decisions)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [Option A: Docker](#option-a-docker-recommended)
  - [Option B: Local development](#option-b-local-development)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [API](#api)
- [Evaluation](#evaluation)
- [Observability](#observability)
- [Project Structure](#project-structure)
- [Development](#development)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Security and Privacy](#security-and-privacy)
- [Contributing](#contributing)
- [Roadmap and Known Limitations](#roadmap-and-known-limitations)
- [License](#license)
- [Acknowledgments](#acknowledgments)

## Features

**Transcription**

- **Synchronous and durable modes.** Audio within
  `STT_MAX_SYNC_DURATION_S` (default 180 s) is transcribed inside the request;
  longer audio becomes a durable job (`202`) executed by a Temporal workflow.
- **Long audio is chunked locally.** Past `STT_CHUNK_SECONDS` the local
  engine decodes the file once, slices it at exact 16 kHz sample offsets,
  transcribes each slice and shifts every slice's transcript back onto the
  original timeline (`domain.timeline.shift_segments`) — timestamps are
  never re-derived. Managed providers are untouched and keep their own
  declared limits.
- **Local-first engine.** Faster-Whisper `large-v3-turbo` on CUDA is the
  default provider in GPU mode; CPU mode ships a smaller model. Model choice,
  device and compute type are configuration, not code.
- **Managed providers behind the same contract.** Deepgram (nova-3),
  ElevenLabs Scribe v2 and Sarvam (saaras batch) register automatically when
  their key is present and disappear from the API when it is not — an
  unconfigured provider is never offered.
- **Timestamps with provenance.** Segment timestamps from every provider;
  word-level timings, per-word confidence and speaker labels from the
  providers that report them. Nothing is fabricated: a provider that cannot
  produce word timing simply does not claim it.
- **Capability and limit truth.** `/v1/providers` reports each engine's real
  capabilities and declared limits (with sources), and the platform refuses
  work a provider would refuse — before any vendor call.
- **Idempotent job creation.** An `Idempotency-Key` header deduplicates
  retries; reuse with different parameters is refused with a conflict.

**Jobs and real-time**

- **Lifecycle control.** `ACCEPTED → VALIDATING → STORED → QUEUED →
  PROCESSING → SUCCEEDED` (plus `RETRY_WAIT`, `FAILED`, `CANCELLED`), with
  retry and cancel endpoints and a `DELETE` that removes stored audio and
  rows in the safe order.
- **Streaming transcription (local-first).** A WebSocket endpoint accepts
  audio frames and emits `partial`/`final` events with timings. Only
  providers whose `streaming` capability has been verified may serve a
  stream; a stream is deliberately not a job — nothing is persisted.
- **Presigned uploads.** Ask for a slot, `PUT` the bytes directly to object
  storage, then create the job against the returned opaque key.

**Measurement and operations**

- **Evaluation with explanations.** Score a dataset against human-verified
  references and get per-clip diffs (substitutions/deletions/insertions),
  aggregate metrics whose formulas the API itself explains, and an
  `insufficient_evidence` flag when a run cannot support its conclusions.
- **OpenTelemetry on by default.** API, provider calls, workflow and
  activity spans; one trace id follows a job across the API/worker boundary.
  An unreachable collector degrades telemetry, never transcription.
- **One-command deployment.** `./launch.sh start` (CPU) or `./launch.sh gpu`
  brings up the full stack behind one origin, generating its own secrets on
  first run. The console is served same-origin; there is no CORS to
  misconfigure and the session cookie is first-party.

**Deliberately not claimed**

- Managed-provider **real-time** streaming: the Deepgram adapter exists and
  is tested against the vendor's protocol, but its `streaming` capability is
  not yet `verified`, so the platform does not offer it. Local streaming is.
- Local diarization: the local model returns no speaker labels; only
  providers whose cards say so return them.
- Autoscaling and queue-depth SLOs: not implemented — see
  [Roadmap](#roadmap-and-known-limitations).

## Screenshots

| | |
|---|---|
| ![Landing page](docs/images/ui-landing-dark.png) | ![Recording with live transcription](docs/images/ui-recorder-preview.png) |
| Marketing and capability overview, driven by the live `/v1/providers` response | Recording with live transcription in progress |
| ![Evaluation results](docs/images/ui-evaluations.png) | ![Provider directory](docs/images/ui-providers.png) |
| Evaluation runs with metric explanations and per-clip diffs | Provider directory: capabilities, provenance, limits |
| ![Login](docs/images/ui-login-dark.png) | ![Mobile layout](docs/images/ui-mobile.png) |
| Session login (demo credentials only in non-production) | The console is usable from a phone |

Light and dark themes are both first-class; more captures live in
[`docs/images/`](docs/images).

## Architecture

```mermaid
graph TB
    subgraph clients["Clients"]
        UI["React console<br/>(served same-origin)"]
        AGENTS["Voice agents / scripts / API clients"]
    end

    subgraph api_proc["API process (FastAPI, :8200)"]
        ROUTES["/v1 routes"]
        AUTH["API-key / session auth<br/>tenant assigned server-side"]
        OBS["OpenTelemetry (on by default)<br/>+ optional Prometheus"]
    end

    subgraph data["Data"]
        PG[("PostgreSQL<br/>jobs · transitions · outbox<br/>evaluations")]
        S3[("Object storage<br/>audio + artifacts<br/>opaque tenant-scoped keys")]
    end

    subgraph durable["Durable execution"]
        OUTBOX["Transactional outbox<br/>at-least-once dispatch"]
        TEMPORAL["Temporal workflow<br/>retries with visible RETRY_WAIT"]
        WORKER["Worker<br/>one attempt = one activity"]
    end

    subgraph prov["Provider adapters — one contract"]
        FW["Faster-Whisper large-v3-turbo<br/>local · CUDA"]
        DG["Deepgram nova-3"]
        EL["ElevenLabs Scribe v2"]
        SV["Sarvam saaras batch"]
    end

    UI --> ROUTES
    AGENTS --> ROUTES
    ROUTES --> AUTH
    ROUTES --> PG
    ROUTES --> S3
    ROUTES -. "long audio → 202" .-> OUTBOX
    OUTBOX --> TEMPORAL
    TEMPORAL --> WORKER
    WORKER --> prov
    ROUTES --> prov
```

**What happens to an upload.** The route validates the audio with ffprobe,
then checks the *effective* limit — this deployment's cap and the provider's
declared cap, whichever is stricter — and refuses before any vendor call.
Short audio is transcribed inline and answered synchronously. Longer audio
is stored under an opaque key, a job row and an outbox row are written in one
transaction, and the dispatcher starts a Temporal workflow whose id derives
from the outbox row, so a redelivery cannot start a second workflow. The
worker's activity re-checks the limits, calls the adapter, and writes the
transcript as a result object plus state transitions. Cancellation is checked
through the workflow's cancellation reason; a killed worker's attempt is
bounded by a timeout and re-delivered.

**Real-time path.** The WebSocket route owns one ffmpeg decoder per session
and emits `ready`/`partial`/`final`/`error`/`done` events. It is local-first
by policy: a provider without a verified `streaming` capability is refused
before any audio is accepted.

**Optional pieces, clearly labelled.** Temporal UI (`:28080`) and the MLflow
profile (`./launch.sh mlflow`, `:5000`) are conveniences; GPU execution
(`./launch.sh gpu`) is opt-in via an NVIDIA runtime. Autoscaling, per-tenant
fair scheduling and queue-depth SLOs are *proposed*, not built.

Deeper reading: [docs/architecture.md](docs/architecture.md) (boundaries,
data model, ADR log), the [architecture diagrams section](https://abdul-jaweed.github.io/stt-platform/architecture/)
(published with their gate receipts) and
[docs/pipeline-and-reliability.md](docs/pipeline-and-reliability.md)
(failure modes, capacity, retention).

## Design Decisions

The choices that shaped everything else, each one still visible in the code:

- **The contract came before the engines.** The canonical transcript schema
  (`schemas/transcript.py`) and the provider contract
  (`providers/base.py` — `transcribe` for sync engines, `submit` / `poll` /
  `cancel` for async ones) were written first, so swapping in a new engine
  never touches the API, the jobs, or the clients.
- **Local-first, and local audio never leaves silently.** The local engine is
  always available and is the default; managed providers register only while
  their key is configured, and only serve a request that explicitly selects
  them. Live preview and realtime streaming are local-only.
- **Capabilities are discovered, never assumed.** `GET /v1/providers` serves
  each engine's real features and declared limits, with sources; a capability
  reads `documented` until one of our own live runs earns `verified`.
- **Nothing is fabricated.** Timestamps come from the engine that produced
  them — a provider that cannot report word timing does not claim it, a
  zero-length word is dropped (never clamped), and a degenerate segment fails
  the job loudly instead of silently losing transcript text.
- **Short jobs are synchronous, long jobs are durable.** Under
  `STT_MAX_SYNC_DURATION_S` a request is answered inline; past it — and for
  every async provider — the work becomes a Temporal workflow fed by a
  transactional outbox: it survives restarts, retries with backoff, and can
  be cancelled.
- **Every timestamp is on the original timeline.** Nothing downstream knows
  how the audio was processed: the local long-audio chunker shifts each slice
  back with `domain/timeline.py::shift_segments` instead of re-deriving
  anything.
- **Storage keeps audio and truth apart.** Audio lives in S3-compatible
  storage under opaque keys with presigned access; job records, transitions
  and transcripts live in PostgreSQL. Deletion removes objects first, then
  rows, and retention is a configured window, not an accident.
- **Tenants come from credentials, never from requests.** The tenant that
  owns a job is resolved server-side from the API key or session;
  client-supplied tenant fields are ignored, and cross-tenant reads look like
  they do not exist.
- **Observability is on by default and allowed to fail quietly.** Tracing and
  metrics never break a transcription; the Grafana dashboard and Prometheus
  alerts ship as operator-owned artifacts (this repo deploys neither).
- **Vendor limits are enforced before the vendor.** An over-limit file is
  refused up front, naming the provider, using the vendor's documented
  numbers — cheaper and clearer than failing in the cloud.

## Tech Stack

- **Backend** — Python 3.13, FastAPI + Pydantic v2, uvicorn, Temporal
  (`temporalio`), PostgreSQL via psycopg, Alembic migrations, Faster-Whisper
  + ctranslate2 (CUDA runtime in the GPU image), OpenTelemetry SDK, MLflow
  client (evaluation logging, optional), respx in provider tests.
- **Frontend** — React + TypeScript, Vite, React Router, Phosphor Icons;
  Vitest + Testing Library + MSW for tests.
- **Infrastructure** — Docker Compose, PostgreSQL, RustFS (S3-compatible
  object storage), Temporal server + UI, FFmpeg/ffprobe.
- **Docs** — MkDocs Material, published to GitHub Pages by a workflow that
  builds strictly on every change.

## Prerequisites

**Option A — Docker (recommended)**

- Docker Engine or Docker Desktop with Compose v2 (`docker compose version`).
- A few GB of free disk for images and the Whisper model, which downloads on
  first use.
- Optional: an NVIDIA GPU with the container toolkit (`nvidia-smi` works, and
  Docker can see the device) for `./launch.sh gpu`. CPU-only machines run the
  smaller model instead.

**Option B — Local development**

- Python **3.13** and [uv](https://docs.astral.sh/uv/).
- Node.js 22 (the version this console is developed against) with npm.
- FFmpeg/ffprobe on `PATH` (audio validation and decoding).
- Docker still used for infrastructure (PostgreSQL, RustFS, Temporal).

Verify what you have:

```bash
docker compose version
python --version      # 3.13.x
uv --version
node --version        # v22.x
ffmpeg -version | head -1
```

## Installation

### Option A: Docker (recommended)

```bash
git clone https://github.com/Abdul-Jaweed/stt-platform.git
cd stt-platform

./launch.sh start     # CPU mode  (or: ./launch.sh gpu for NVIDIA passthrough)
```

The launcher builds the images, generates a `.env` with a console password
and a session secret on first run, starts the stack, and waits until the API
reports ready. No configuration is required for a local evaluation.

| Service | URL / port |
|---|---|
| Console (nginx, same origin) | <http://127.0.0.1:8080> |
| API | <http://127.0.0.1:8200> |
| Interactive API docs | <http://127.0.0.1:8200/docs> |
| PostgreSQL | `127.0.0.1:25432` |
| RustFS (S3-compatible) | `127.0.0.1:29000` (console `:29001`) |
| Temporal | `127.0.0.1:7233` (UI `:28080`) |
| MLflow (opt-in profile) | `./launch.sh mlflow` → `:5000` |

**Sign in.** Username `stt`; the password was generated for you — read it
with:

```bash
grep STT_UI_PASSWORD .env
```

**Manage the stack:**

```bash
./launch.sh status       # what is running and healthy
./launch.sh logs api     # follow logs (any service name works)
./launch.sh restart      # recreate api, worker and console
./launch.sh rebuild      # rebuild images, then bring the stack back up
./launch.sh stop         # stop containers, keep data volumes
./launch.sh clean        # delete containers, data volumes and both images (asks first)
```

**Provider keys.** To enable a managed provider, put its key in `.env`
(`STT_DEEPGRAM_API_KEY`, `STT_ELEVENLABS_API_KEY`, `STT_SARVAM_API_KEY`) and
run `./launch.sh restart`. A provider without a key is simply not offered —
`/v1/providers` lists exactly what is configured and healthy.

**Installation validation.** All five checks should pass on a fresh clone:

```bash
curl -s http://127.0.0.1:8200/v1/health/ready
# {"status":"ready"}

curl -s http://127.0.0.1:8200/v1/providers | head -c 400
# lists at least faster-whisper on a GPU/CPU start

curl -s http://127.0.0.1:8080/ | grep -o "<title>[^<]*</title>"
# <title>STT Platform — provider-neutral transcription</title>

curl -s -X POST http://127.0.0.1:8200/v1/transcriptions \
     -F "file=@audio/sample.wav" -F "provider=faster-whisper" | head -c 300
# 201 with a job record whose status ends at SUCCEEDED for this 10-second sample

docker compose ps | grep -c healthy
# every long-running service reports healthy
```

### Option B: Local development

Infrastructure stays in Docker; the API, worker and console run on your
machine.

```bash
# 1. Infrastructure only (from the repository root)
docker compose up -d postgres rustfs storage-init temporal-postgres temporal

# 2. Backend (first terminal)
cd backend
uv sync                      # creates the venv from uv.lock
uv run alembic upgrade head  # apply the database schema
uv run uvicorn stt_platform.main:app --host 127.0.0.1 --port 8200 --reload

# 3. Worker (second terminal) — needed for durable (202) jobs
cd backend
uv run python -m stt_platform.workers.transcription_worker

# 4. Console (third terminal)
cd frontend
npm ci
npm run dev                  # Vite on http://127.0.0.1:5173
```

The backend's defaults already point at the compose infrastructure
(`127.0.0.1:25432`, `127.0.0.1:29000`, `127.0.0.1:7233`), and the default CORS
origins include the Vite dev server. Override anything by creating
`backend/.env` (same `STT_`-prefixed names as in
[Configuration](#configuration)) or by exporting the variables.

Notes:

- **Model download** happens on first transcription (Hugging Face cache;
  `STT_HF_HOME` overrides the location). CPU development is fine with
  `STT_WHISPER_MODEL=small STT_DEVICE=cpu STT_COMPUTE_TYPE=int8`.
- **GPU outside Docker** needs the CUDA runtime libraries that the GPU image
  bundles; the Python package `ctranslate2` does not carry them.
- Migrations are Alembic (`backend/alembic/`); `alembic upgrade head` is what
  the compose `migrate` service runs.

## Quick Start

```bash
git clone https://github.com/Abdul-Jaweed/stt-platform.git
cd stt-platform
./launch.sh start                                  # or: ./launch.sh gpu

curl -s http://127.0.0.1:8200/v1/health/ready      # {"status":"ready"}
# then open http://127.0.0.1:8080 — console, user `stt`, password in .env
```

First transcription from the command line:

```bash
curl -s -X POST http://127.0.0.1:8200/v1/transcriptions \
     -F "file=@audio/sample.wav" -F "provider=faster-whisper"
```

The response is a job record; take its `id` and fetch the transcript:

```bash
curl -s http://127.0.0.1:8200/v1/transcriptions/<id>/result | python -m json.tool
```

## Configuration

Every setting is an environment variable with the `STT_` prefix (the field
names live in [`backend/src/stt_platform/core/config.py`](backend/src/stt_platform/core/config.py)).
For the Docker stack, put them in the repository-root `.env` — the launcher
creates it on first run, and the compose file forwards every documented
variable (enforced by `backend/tests/unit/test_compose_env.py`). For local
development, use `backend/.env` or exported variables.

Anything not listed here has a safe default; a handful of internal knobs
(path overrides, sampling intervals) live in `config.py` itself.

**Core**

| Variable | Description | Required | Default |
|---|---|---|---|
| `STT_ENVIRONMENT` | Deployment label. `production` hides demo credentials and enables production safety rules | No | `local` |
| `STT_LOG_LEVEL` | Log level | No | `INFO` |
| `STT_LOG_JSON` | One redacted JSON object per log line instead of plain text | No | `false` |
| `STT_MAX_REQUESTS_PER_MINUTE` | Built-in request throttle per client; `0` disables | No | `0` |
| `STT_CORS_ORIGINS` | Browser origins allowed to call the API directly (the compose stack is same-origin and needs none) | No | Vite dev + preview origins |

**Database and object storage**

| Variable | Description | Required | Default |
|---|---|---|---|
| `STT_DATABASE_URL` | PostgreSQL DSN | No | `postgresql+psycopg://stt:stt@127.0.0.1:25432/stt` |
| `STT_S3_ENDPOINT` | S3-compatible endpoint | No | `http://127.0.0.1:29000` |
| `STT_S3_ACCESS_KEY` / `STT_S3_SECRET_KEY` | Object storage credentials — **change these for any shared deployment** | No (dev defaults) | `stt` / `stt123456` |
| `STT_S3_REGION` | S3 region | No | `us-east-1` |
| `STT_S3_BUCKET_AUDIO` / `STT_S3_BUCKET_ARTIFACTS` | Bucket names for audio and evaluation artifacts | No | `stt-audio` / `stt-artifacts` |
| `STT_PRESIGN_EXPIRY_S` | Lifetime of presigned upload URLs | No | `900` |
| `STT_RETENTION_DAYS` | Delete terminal jobs older than this (objects first, then rows); `0` keeps everything | No | `0` |

**Jobs, retries and Temporal**

| Variable | Description | Required | Default |
|---|---|---|---|
| `STT_MAX_SYNC_DURATION_S` | Longest audio transcribed inside the request; longer audio gets a durable job (`202`) | No | `180` |
| `STT_MAX_ASYNC_DURATION_S` | Hard cap for durable jobs | No | `14400` (4 h) |
| `STT_RETRY_MAX_ATTEMPTS` | Workflow attempts per job | No | `3` |
| `STT_RETRY_BASE_DELAY_S` / `STT_RETRY_MULTIPLIER` / `STT_RETRY_MAX_DELAY_S` / `STT_RETRY_JITTER_RATIO` | Retry backoff schedule | No | `2.0` / `2.0` / `60.0` / `0.25` |
| `STT_ACTIVITY_ATTEMPT_TIMEOUT_S` | Upper bound for one attempt before the worker must re-deliver | No | `1800` |
| `STT_TEMPORAL_HOST` / `STT_TEMPORAL_TASK_QUEUE` | Temporal endpoint and task queue | No | `127.0.0.1:7233` / `stt-transcription` |
| `STT_DISPATCH_POLL_INTERVAL_S` | Outbox dispatcher polling interval | No | `2.0` |

**Worker and models**

| Variable | Description | Required | Default |
|---|---|---|---|
| `STT_WHISPER_MODEL` | Local model id (the CPU compose mode uses `small`) | No | `large-v3-turbo` |
| `STT_DEVICE` | `cuda` or `cpu` | No | `cuda` (compose CPU mode sets `cpu`) |
| `STT_COMPUTE_TYPE` | ctranslate2 compute type | No | `float16` (CPU mode: `int8`) |
| `STT_GPU_CONCURRENCY` | Parallel local transcriptions per worker | No | `1` |
| `STT_CHUNK_SECONDS` | Local long-audio chunking slice length (`0` disables) | No | `0` (library default; the compose stack ships `600`) |
| `STT_MANAGED_CONCURRENCY` | Parallel managed-provider calls | No | `4` |

**Provider credentials** (each enables its provider; absent = not offered)

| Variable | Description | Required | Default |
|---|---|---|---|
| `STT_DEEPGRAM_API_KEY` | Deepgram (nova-3) | To use Deepgram | — |
| `STT_ELEVENLABS_API_KEY` | ElevenLabs Scribe v2 | To use ElevenLabs | — |
| `STT_ELEVENLABS_MODEL_ID` | ElevenLabs model id | No | `scribe_v2` |
| `STT_SARVAM_API_KEY` | Sarvam (saaras batch) | To use Sarvam | — |
| `STT_*_BASE_URL` | Override a vendor's API base (proxy/testing) | No | vendor defaults |
| `STT_PROVIDER_TIMEOUT_S` / `STT_PROVIDER_CONNECT_TIMEOUT_S` | Per-call timeouts | No | `300` / `10` |
| `STT_REMOTE_POLL_INTERVAL_S` / `STT_REMOTE_MAX_WAIT_S` | Polling for async vendors (e.g. Sarvam batch) | No | `5` / `7200` |
| `STT_MAX_PROVIDER_RESPONSE_BYTES` | Response size guard | No | `25000000` |
| `STT_MAX_UPLOAD_BYTES` | Upload size cap | No | `104857600` (100 MiB) |
| `STT_MAX_LIVE_PREVIEW_DURATION_S` | Cap for the HTTP live-preview endpoint | No | `120` |

**Streaming**

| Variable | Description | Required | Default |
|---|---|---|---|
| `STT_STREAM_MAX_DURATION_S` | Hard cap per stream session | No | `300` |
| `STT_STREAM_IDLE_TIMEOUT_S` | Close an idle stream | No | `30` |
| `STT_STREAM_MAX_SESSIONS` | Concurrent stream sessions per process; a busy limiter answers `STREAM_BUSY` | No | `2` |
| `STT_STREAM_MAX_FRAME_BYTES` | Largest accepted frame | No | `1048576` |

**Authentication and UI**

| Variable | Description | Required | Default |
|---|---|---|---|
| `STT_API_KEYS` | API keys for machine clients and their tenant scopes (syntax in [docs/deployment.md](docs/deployment.md)); unset leaves the local API open | To secure the API | empty |
| `STT_UI_AUTH_ENABLED` | Require login for the console | No | `true` |
| `STT_UI_USERNAME` / `STT_UI_PASSWORD` | Console credentials — **the password is generated by `launch.sh` on first run** | Auto | `stt` / generated |
| `STT_UI_PASSWORD_HASH` | Alternative to a plaintext password | No | — |
| `STT_UI_TENANT` | Tenant that console sessions act as | No | `local` |
| `STT_SESSION_SECRET` | Cookie signing key — **generated by `launch.sh`** | Auto | generated |
| `STT_SESSION_COOKIE_NAME` / `STT_SESSION_TTL_S` | Session cookie name and lifetime (seconds) | No | `stt_session` / `43200` |
| `STT_SESSION_COOKIE_SECURE` | Mark the cookie `Secure` (set `true` behind TLS) | No | `false` |
| `STT_AUTH_DEMO_MODE` | Show the demo-credentials panel on the login page — **never shown when `STT_ENVIRONMENT=production`** | No | `true` |
| `STT_LOGIN_MAX_ATTEMPTS_PER_MINUTE` | Login rate limit | No | `10` |

**Observability and evaluation**

| Variable | Description | Required | Default |
|---|---|---|---|
| `STT_OTEL_ENABLED` | Tracing. On by default; set `false` to disable | No | `true` |
| `STT_OTEL_ENDPOINT` | OTLP/HTTP collector endpoint (bring your own collector) | No | `http://127.0.0.1:4318` |
| `STT_OTEL_EXPORTER` / `STT_OTEL_SAMPLE_RATIO` | Exporter (`otlp`) and sampling ratio | No | `otlp` / `1.0` |
| `STT_OTEL_SERVICE_NAME` / `STT_OTEL_ENVIRONMENT` | `service.name` and deployment environment on spans | No | `stt-platform` / `local` |
| `STT_PROMETHEUS_ENABLED` / `STT_PROMETHEUS_PORT` | Prometheus metrics endpoint (per process; API and worker need distinct ports) | No | `false` / `9464` |
| `STT_MLFLOW_ENABLED` / `STT_MLFLOW_TRACKING_URI` / `STT_MLFLOW_EXPERIMENT` / `STT_MLFLOW_TIMEOUT_S` | Evaluation logging to MLflow. Best-effort by contract: a dead tracking server never fails an evaluation | No | `true` / `http://127.0.0.1:5000` / `stt-evaluation` / `10` |
| `STT_EVALUATION_MAX_CLIPS` | Cap per evaluation run | No | `25` |
| `STT_EVALUATION_DATASETS_DIR` / `STT_EVALUATION_AUDIO_ROOT` | Override where datasets and corpus audio are read from | No | repository defaults |

## Usage

All examples below run against the local stack (`http://127.0.0.1:8200`;
through the console origin it is `http://127.0.0.1:8080/v1/...`). If you
configured `STT_API_KEYS`, add `-H "X-API-Key: <key>"`.

**1 — What is configured, and what it can do**

```bash
curl -s http://127.0.0.1:8200/v1/providers
```

```json
{
  "providers": [
    {
      "id": "faster-whisper",
      "name": "Faster-Whisper (local)",
      "is_local": true,
      "capabilities": { "streaming": true, "word_timestamps": true, "diarization": false, "...": "..." },
      "health": { "status": "ok" }
    },
    {
      "id": "deepgram",
      "name": "Deepgram (nova-3)",
      "is_local": false,
      "capabilities": { "word_timestamps": true, "diarization": true, "max_file_bytes": 2147483648, "...": "..." },
      "health": { "status": "ok" }
    }
  ]
}
```

Only configured providers appear. An empty key means an empty list — nothing
is advertised before it can actually run.

**2 — Transcribe a file (synchronous)**

```bash
curl -s -X POST http://127.0.0.1:8200/v1/transcriptions \
     -H "Idempotency-Key: demo-0001" \
     -F "file=@audio/sample.wav" \
     -F "provider=faster-whisper" \
     -F "language=en"
```

`201` returns a job record; `202` means the audio exceeded the sync cap and
the job is now durable. Trimmed record (the real one carries audio metadata,
timings and failure details):

```json
{
  "id": "f9ad66a5-...",
  "status": "SUCCEEDED",
  "requested_provider": "faster-whisper",
  "actual_provider": "faster-whisper",
  "model": "large-v3-turbo",
  "language_detected": "en",
  "attempt_count": 1
}
```

**3 — Read the transcript**

```bash
curl -s http://127.0.0.1:8200/v1/transcriptions/<id>/result | python -m json.tool
```

```json
{
  "job_id": "f9ad66a5-...",
  "text": "…full transcript…",
  "language": "en",
  "timestamp_granularity": "word",
  "segments": [
    { "start": 0.0, "end": 4.2, "text": "…", "words": [ { "start": 0.0, "end": 0.4, "text": "…", "confidence": 0.98 } ] }
  ],
  "audio_duration_s": 10.0,
  "processing_duration_s": 1.1,
  "provider": "faster-whisper",
  "model": "large-v3-turbo",
  "warnings": []
}
```

`confidence` and `speaker` appear only when the provider actually reports
them; timestamps always refer to the original audio timeline.

**4 — Long audio, idempotency and lifecycle**

```bash
# longer than STT_MAX_SYNC_DURATION_S → 202, executed by the worker
curl -s -X POST http://127.0.0.1:8200/v1/transcriptions -F "file=@long.wav"
# repeat the same call with the same Idempotency-Key → same job, not a new one

curl -s -X POST http://127.0.0.1:8200/v1/transcriptions/<id>/cancel   # CANCELLED
curl -s -X POST http://127.0.0.1:8200/v1/transcriptions/<id>/retry    # after FAILED
curl -s -X DELETE http://127.0.0.1:8200/v1/transcriptions/<id>       # 204, objects then rows
```

**5 — Upload directly to storage (presigned flow)**

```bash
# ask for a slot (filename optional)
curl -s -X POST http://127.0.0.1:8200/v1/transcriptions/uploads/presign \
     -H "Content-Type: application/json" -d '{"filename": "meeting.m4a"}'
```

```json
{ "object_key": "…opaque…", "upload_url": "http://127.0.0.1:29000/…", "method": "PUT", "expires_in_s": 900 }
```

```bash
curl -s -X PUT --upload-file meeting.m4a "<upload_url>"
curl -s -X POST http://127.0.0.1:8200/v1/transcriptions -F "object_key=<object_key>"
```

The key is opaque and scope-bound: it never reveals the storage layout, and
it is only accepted from the tenant that asked for it.

**6 — Real-time transcription (WebSocket, local-first)**

```python
# pip install websockets
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8080/v1/transcriptions/stream") as ws:
        await ws.send(json.dumps({"type": "start", "provider": "faster-whisper",
                                  "input_format": "pcm16", "language": "en"}))
        print(json.loads(await ws.recv()))          # {"type": "ready", ...}
        # send raw 16 kHz mono s16le bytes as binary frames
        # await ws.send(pcm_bytes)
        await ws.send(json.dumps({"type": "stop"}))
        async for message in ws:
            print(message)                           # partial → final → done

asyncio.run(main())
```

`partial` events arrive while audio is still being sent; `final` events
carry timings and are never revised. A provider that cannot serve streams is
refused before any audio is accepted — the console uses this endpoint with
an HTTP preview fallback only when no socket can be opened.

**7 — Run an evaluation**

```bash
curl -s -X POST http://127.0.0.1:8200/v1/evaluations \
     -H "Content-Type: application/json" \
     -d '{"dataset_id": "stt-eval-core", "provider": "faster-whisper"}'
```

The run is synchronous and returns its record (`201`): per-clip summaries,
aggregates, normalization rules, and `insufficient_evidence` when the run
cannot support a conclusion. Fetch the per-clip diff document separately
with `GET /v1/evaluations/<evaluation_id>/artifact`.

## API

- **Base URL** — `http://127.0.0.1:8200` directly, or `http://127.0.0.1:8080/v1/...`
  through the console's same-origin proxy.
- **Versioning** — everything is under `/v1`; transcripts carry their own
  `schema_version`.
- **Auth** — machine clients send `X-API-Key`; the server assigns the tenant
  from the key (a client-supplied tenant is never trusted). The console uses
  a signed `HttpOnly`, `SameSite=Lax` session cookie. With `STT_API_KEYS`
  unset the local API is open — intended for local development only.
- **Errors** — every error body is `{"error": {"code", "message"}}`
  (validation failures use the same envelope). Cross-tenant reads answer
  `404`, never `403`: the existence of another tenant's job is not disclosed.
- **Interactive docs** — FastAPI serves `/docs` from the same schema that is
  committed at [docs/openapi.json](docs/openapi.json); the route table is
  drift-tested against it, and a walkthrough with verified example runs is in
  [docs/api.md](docs/api.md).
- **Pagination** — none: list endpoints return their bounded results whole.
- **Rate limits** — off by default (`STT_MAX_REQUESTS_PER_MINUTE=0`); login
  attempts are limited to `STT_LOGIN_MAX_ATTEMPTS_PER_MINUTE` (default 10).

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/health/live` | Liveness |
| GET | `/v1/health/ready` | Readiness (checks PostgreSQL) |
| GET | `/v1/auth/config` | Auth and demo-mode configuration for the console |
| POST | `/v1/auth/login` | Console login (sets the session cookie) |
| POST | `/v1/auth/logout` | End the session |
| GET | `/v1/auth/me` | Current session |
| GET | `/v1/providers` | Provider directory with capabilities, provenance and limits |
| POST | `/v1/transcriptions` | Create a job (multipart `file` or `object_key`) |
| POST | `/v1/transcriptions/uploads/presign` | Presigned PUT target for direct upload |
| GET | `/v1/transcriptions/{id}` | Job record |
| GET | `/v1/transcriptions/{id}/result` | Transcript with segments and timestamps |
| POST | `/v1/transcriptions/{id}/cancel` | Cancel |
| POST | `/v1/transcriptions/{id}/retry` | Retry a failed job |
| DELETE | `/v1/transcriptions/{id}` | Delete job, audio and rows (`204`) |
| POST | `/v1/transcriptions/preview` | Local-only live preview (`is_final: false`, never persisted) |
| WS | `/v1/transcriptions/stream` | Streaming transcription |
| GET | `/v1/evaluations` | List evaluation runs |
| POST | `/v1/evaluations` | Create and run an evaluation (synchronous) |
| GET | `/v1/evaluations/metrics` | Metric explanations (formulas, direction, edge behaviour) |
| GET | `/v1/evaluations/{id}` | Evaluation record |
| GET | `/v1/evaluations/{id}/artifact` | Per-clip diff document |

**Upload constraints** — validated with ffprobe before anything is stored:
file size ≤ `STT_MAX_UPLOAD_BYTES` (100 MiB default); the audio must decode;
duration must fit the sync cap (inline) or the async cap (durable); and the
provider's own declared limits are checked and refused locally first.

## Evaluation

Evaluation is a measurement, not an opinion. Reference transcripts are
human-verified; synthetic clips can be scored for smoke tests, but a clip
marked `pending-human-verification` is **never** scored — it is reported as
unscored instead. A metric that could not be computed is `null` and rendered
as "not measured", never as `0.0`.

Text is normalized with the recorded `stt-normalize-v1` rules (Unicode NFKC,
lowercase, punctuation stripped, whitespace collapsed; deliberately no number
expansion and no filler removal) before alignment.

| Metric | Applies to | Direction | Formula |
|---|---|---|---|
| `wer` | per clip | lower is better | (substitutions + deletions + insertions) / reference words |
| `cer` | per clip | lower is better | (substitutions + deletions + insertions) / reference characters (spaces removed) |
| `wer_micro` | run aggregate | lower is better | sum(errors over scored clips) / sum(reference words over scored clips) |
| `wer_macro` | run aggregate | lower is better | mean(wer per scored clip) |
| `cer_micro` | run aggregate | lower is better | sum(character errors) / sum(reference characters) over scored clips |
| `cer_macro` | run aggregate | lower is better | mean(cer per scored clip) |
| `failure_rate` | run aggregate | lower is better | clips with no accuracy / clips attempted |
| `rtf_mean` | run aggregate | lower is better | mean(processing seconds / audio seconds), per clip |
| `coverage_mean` | run aggregate | higher is better | mean(fraction of reference words covered by a timestamp) |

Aggregates are computed over **scored** clips; failures are visible through
`failure_rate` rather than silently dragging averages down. Micro and macro
disagree when clip lengths differ — both are reported so you can see it.

`GET /v1/evaluations/metrics` serves exactly this table from the same code
that computes the numbers; the console renders each metric card with the
server's own explanation, and a metric without one is flagged rather than
explained away. Deeper reading: [docs/evaluation.md](docs/evaluation.md).

## Observability

- **Tracing is on by default** (`STT_OTEL_ENABLED=false` opts out) and has
  never been allowed to break a transcription: an unreachable collector
  degrades telemetry, not the queued job.
- **Instrumented**: the API's server spans, the start of a job workflow, the
  worker's activity, and every provider call. Trace context propagates across
  the API → Temporal boundary — one trace id covers the whole job, proven by
  [`scripts/observability_evidence.py`](scripts/observability_evidence.py)
  against the live stack.
- **Redaction**: logs pass through a redaction filter (and a test scans the
  tree for secrets); spans carry identifiers, providers, outcomes and counts
  (`stt.job_id`, `stt.outcome`, HTTP method/route/status) — never audio or
  transcript text.
- **Bring your own collector**: point `STT_OTEL_ENDPOINT` at any OTLP/HTTP
  endpoint. The project deliberately does not pin a collector image — the
  choice (and the retention rules) belong to your deployment.
- **Troubleshooting missing traces**: check `STT_OTEL_ENABLED`, that the
  endpoint resolves from *inside* the containers (localhost is not your
  host), and the service logs for exporter errors — dropped traces are
  reported, never silently retried forever.

Prometheus metrics are opt-in (`STT_PROMETHEUS_ENABLED`); the API and worker
are separate processes, so give them distinct `STT_PROMETHEUS_PORT`s. The
Grafana dashboard (20 panels) and the Prometheus alert rules (18) ship with
the package —
[`observability/dashboards/stt-overview.json`](backend/src/stt_platform/observability/dashboards/stt-overview.json)
and
[`observability/alerts/stt-alerts.yaml`](backend/src/stt_platform/observability/alerts/stt-alerts.yaml).
They are artifacts **for an operator-owned stack**: this repository deploys
the STT platform only, never Grafana or Prometheus — and a guard test pins
every metric they reference to the exporter's registry, so a renamed metric
breaks the build instead of the dashboard.

More: [docs/observability.md](docs/observability.md) (scrape config and the
import walkthrough).

## Project Structure

```text
stt-platform/
├── backend/
│   ├── src/stt_platform/
│   │   ├── api/           # FastAPI app, /v1 routes, auth, error envelope
│   │   ├── services/      # job orchestration, streaming session wiring
│   │   ├── workers/       # Temporal worker + activity (one attempt)
│   │   ├── workflows/     # transcription workflow (retries, cancellation)
│   │   ├── providers/     # faster-whisper + managed adapters, registry
│   │   ├── streaming/     # VAD windows, session core, ffmpeg PCM decoder
│   │   ├── persistence/   # SQLAlchemy rows, outbox, repositories
│   │   ├── evaluation/    # datasets, runner, metrics + explanations
│   │   ├── audio/         # ffprobe validation, limits enforcement
│   │   ├── observability/ # tracing, metrics, redaction
│   │   ├── domain/        # statuses, errors, transitions
│   │   ├── schemas/       # API request/response models
│   │   └── core/          # settings, logging, CUDA checks
│   ├── alembic/           # migrations
│   └── tests/             # unit/ + integration/ (testcontainers)
├── frontend/
│   ├── src/               # pages, components, hooks, api client, styles
│   └── src/*.test.tsx     # Vitest + Testing Library + MSW
├── docs/                  # guides, phase reports, OpenAPI, architecture/
├── scripts/               # e2e drivers, evidence scripts, corpus builder
├── infrastructure/        # infrastructure-only compose file
├── audio/                 # a 10-second sample used by examples and tests
├── docker-compose.yml     # the full stack (+ docker-compose.gpu.yml override)
└── launch.sh              # one-command start/stop/status/clean
```

Root-level `record.py`, `test_vad.py`, `benchmark_*.py` and friends are
early exploration scripts kept for reference; they are not part of the
service. `models/`, `results/`, `output/`, `my_recording/` and `site/` are
local build artifacts or private material and are gitignored.

## Development

```bash
# Backend
cd backend
uv sync
uv run ruff check src tests        # lint (the configured gate)
uv run pytest -q                   # unit tests (fast)
TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1 uv run pytest -q   # full suite, integration included

# Frontend
cd frontend
npm ci
npm run dev                        # Vite dev server on :5173, talking to the API origins
npm run build                      # tsc --noEmit && vite build
npm test                           # Vitest
```

**Conventions**

- Tests come first: every behavior change starts as a failing test that is
  watched to fail. The suite is the spec — if a doc and a test disagree, the
  test wins and the doc gets fixed.
- Lint and tests must pass before a pull request; `ruff check` is the
  configured Python gate (there is no mypy step and no machine-enforced
  formatting in this repository yet), and the frontend build is
  type-checked by `tsc`.
- Commit messages explain the *why*; UI icons are Phosphor only (enforced by
  a test); CSS custom properties may not reference themselves (also
  enforced).

**Debugging**

- `./launch.sh logs api worker` follows the backend logs.
- Temporal UI at `http://127.0.0.1:28080` shows every workflow, attempt and
  retry of a job — the fastest way to see a stuck transcription.
- `curl -s localhost:8200/v1/health/ready` is the readiness gate used by the
  launcher itself.
- PostgreSQL is reachable at `127.0.0.1:25432` with the `STT_DATABASE_URL`
  credentials.

## Testing

| What | Command |
|---|---|
| Backend, everything | `cd backend && TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1 uv run pytest -q` |
| Backend, unit only | `uv run pytest tests/unit -q` |
| Backend, integration (needs Docker) | `uv run pytest tests/integration -q` with `TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1` |
| GPU tests (real CUDA + model) | `uv run pytest tests/gpu -q` on a GPU machine |
| Live provider calls (spends real money) | `uv run pytest -m live_provider` with the vendor keys set |
| Lint | `uv run ruff check src tests` |
| Frontend | `cd frontend && npm test` |
| Frontend, production build | `npm run build` |
| Docs, strict | `uvx --with mkdocs-material mkdocs build --strict` |
| Compose/env consistency | `uv run pytest tests/unit/test_compose_env.py` |
| End-to-end drivers (live stack) | `uv run python ../scripts/e2e_phase7.py` / `e2e_phase8.py` / `observability_evidence.py` |

Notes: integration tests spin ephemeral PostgreSQL/RustFS containers
(testcontainers) — they need Docker and are the slowest part of the suite.
Managed-provider tests never call a vendor in ordinary CI: they run against
`respx` mocks, and the live calls are a separate, explicitly marked suite
that was run exactly once per provider while a human watched the bill.
Audio fixtures are synthesized at test time with ffmpeg; the only real
recordings in the repository are the 10-second sample and generated corpus.

## Troubleshooting

**The stack does not come up / `waiting for the API` never finishes.**
Run `./launch.sh status` and `./launch.sh logs api`. On a first GPU start the
worker downloads the Whisper model (minutes, once); the API itself should be
ready before that. The launcher's readiness probe is the same
`/v1/health/ready` you can curl yourself.

**A port is already in use.** The default host ports are listed in
[Installation](#option-a-docker-recommended). Stop whatever holds the port,
or change the mapping in `docker-compose.yml` and `./launch.sh restart`.

**The console loads but login fails with 502.** The console's nginx resolves
the API at request time; after manually recreating containers, the upstream
can be stale for a few seconds — retry, then `./launch.sh restart` if it
persists. If it continues, confirm the API is actually healthy
(`docker compose ps`).

**A configured provider is missing from `/v1/providers`.** That is by
design: a provider without its key is not offered at all. Add
`STT_<VENDOR>_API_KEY` to `.env` and `./launch.sh restart`. A provider that
*is* listed but failing health shows it in its record rather than
disappearing.

**A managed provider returns 401/403.** Check the key's permissions in the
vendor's dashboard, not just its existence — some vendors scope keys so that
account endpoints fail while transcription works.

**`AUDIO_TOO_LONG`.** The audio exceeds the *effective* cap — this
deployment's `STT_MAX_*` or the provider's own declared limit, whichever is
stricter — and the error names the provider it refused. Resend a shorter
clip, raise the cap, or pick a provider whose card shows a higher limit.

**The audio is rejected before any provider call.** ffprobe could not decode
it, or its size exceeds `STT_MAX_UPLOAD_BYTES`. Convert with
`ffmpeg -i input -ar 16000 -ac 1 output.wav` and retry.

**A job sits in `QUEUED`.** The worker (or Temporal) is down. Check
`./launch.sh status`; Temporal UI at `:28080` shows whether the workflow was
started at all. A started workflow with no activity means the worker is not
polling its task queue.

**A job went `FAILED`.** Read `failure.code`, `failure.message` and
`failure.retryable` on the job record. `POST …/retry` is honest about this:
it is for failures that can actually change outcome. `RETRY_WAIT` means the
workflow is already retrying — watch it in Temporal UI.

**No traces arrive.** See [Observability](#observability): check
`STT_OTEL_ENABLED`, make sure the collector endpoint is reachable *from
inside the containers*, and check service logs for exporter errors. Dropped
telemetry never affects jobs.

**Frontend on `npm run dev` cannot reach the API.** The Vite dev server talks
to the API origins directly; add your origin to `STT_CORS_ORIGINS` if you
changed the port.

**Windows note.** If `localhost` hangs but `127.0.0.1` works (IPv6 `::1`
resolving first), use `127.0.0.1` in every URL. The Microsoft Store's
`python3` alias is a stub — use `uv`'s managed interpreter.
(Also: in git-bash, `curl -o /dev/null` can exit 23 even on HTTP 200 — use
`curl -s -o file` or check the status code.)

## Security and Privacy

**Implemented controls**

- **Tenant isolation is server-side.** The tenant comes from the presented
  API key or the session cookie — never from the client. A cross-tenant read
  answers `404`, not `403`: the existence of another tenant's job is not
  disclosed. Object keys are opaque and scope-bound, so storage layout and
  other tenants' data are not addressable.
- **The console never holds a secret.** The session is a signed `HttpOnly`,
  `SameSite=Lax` cookie; the password is verified server-side (hash
  supported via `STT_UI_PASSWORD_HASH`), and cookies are first-party because
  the console is served same-origin.
- **Demo credentials are double-gated.** The login demo panel requires
  `STT_AUTH_DEMO_MODE=true` *and* a non-production environment; with
  `STT_ENVIRONMENT=production` it cannot appear at all.
- **Login rate limiting** (`STT_LOGIN_MAX_ATTEMPTS_PER_MINUTE`) and an
  optional request throttle are built in.
- **Secrets hygiene**: `.env` files are gitignored; a unit test scans the
  tree for secret-shaped values; logs pass a redaction filter; audio and
  transcript content never reaches logs or traces.
- **Deletion and retention**: `DELETE` removes stored objects first, then
  rows; `STT_RETENTION_DAYS` (and `scripts/purge_expired.py`) expire terminal
  jobs on the same rule.
- **Non-root containers**: the API, worker and console run as an unprivileged
  user (uid 1000).

**Recommended before exposing this beyond your machine**

- Set `STT_API_KEYS` and use TLS in front (then set
  `STT_SESSION_COOKIE_SECURE=true` and `STT_ENVIRONMENT=production`).
- Change the development S3 credentials and the database password.
- The compose file publishes infrastructure ports (PostgreSQL, RustFS,
  Temporal) for local convenience — restrict them with a firewall or bind
  them to loopback in any shared environment.
- Rotate provider keys like any other credential; never commit them.

## Contributing

Contributions are welcome. The workflow:

1. Fork the repository and clone your fork.
2. Create a branch (`git checkout -b feature/short-description`).
3. Set up the development environment ([Development](#development)).
4. **Write the failing test first**, watch it fail, then implement.
5. Run the gates: `uv run ruff check src tests` and the backend suite;
   `npm test` and `npm run build` for the frontend.
6. Commit with a message that explains *why*, and open a pull request
   describing the change and the evidence.

There is no `CONTRIBUTING.md` or issue template yet — issues and pull
requests through GitHub are the current process, and this file's conventions
are the rules. Small, well-tested changes land fastest. If you are adding a
provider, the adapter contract plus the provenance rules (a capability is
`documented` until a recorded live run proves it) are the two things review
will focus on.

## Roadmap and Known Limitations

**Completed** — eight build phases (durable processing, managed providers,
observability, evaluation, hardening, streaming) plus a product revamp; each
has a report in [`docs/`](docs/) and the revamp ledger is
[docs/evidance-revamp-report.md](docs/evidance-revamp-report.md).

**In progress / partial**

- Managed-provider **real-time** streaming: the contract and the Deepgram
  adapter are implemented and tested against the vendor protocol, but the
  capability flips to `verified` only after one recorded live run — until
  then the platform does not offer it.
- A from-scratch `./launch.sh clean` → `./launch.sh start` validation cycle
  has not been executed on CI hardware; the script is exercised manually.

**Planned**

- Deepgram real-time rollout (then others), per-tenant fair scheduling,
  autoscaling and queue-depth SLOs, and an explicit license file (see
  below).

**Known limitations**

- The local model provides no speaker diarization; only providers whose
  capability cards say so return speakers.
- Rate limiting is in-process: replicas each enforce their own window.
- Evaluation is only as strong as its references: one corpus clip is
  `pending-human-verification` and is therefore reported unscored
  (currently 8 of 9 clips scored).
- The architecture section deliberately contains two delivered diagrams and
  one *authored but not delivered* draft (failure-recovery workflow); the
  reason is stated on the section's page rather than hidden.
- MLflow logging is best-effort by contract: a dead tracking server never
  fails an evaluation.

**Blocked by external configuration** — managed providers need their keys;
GPU mode needs an NVIDIA runtime; MLflow needs a tracking server.

## License

**This repository has no license file.** That means the code is
"all rights reserved" by default: it is public for reading, evaluation and
reference, but reuse, modification or redistribution needs a license first.
Adding one is the owner's decision — a permissive license (MIT, Apache-2.0)
or a copyleft one (AGPL-3.0) would each fit a project shaped like this one —
and until it exists, please do not present derivative work as licensed.
This section will be replaced with the real terms when the file lands.

## Acknowledgments

STT Platform builds on excellent open source: Faster-Whisper and ctranslate2
(the local engine), OpenAI's Whisper model weights, FastAPI, Pydantic,
uvicorn, uv, Temporal (temporalio), PostgreSQL, RustFS, FFmpeg, React, Vite
and Phosphor Icons, MkDocs Material for the documentation site,
OpenTelemetry for tracing, and MLflow for optional experiment logging. The
published architecture diagrams were produced with the internal `archify`
tooling, and their gate receipts ship next to them.

---

*Every claim in this README was checked against the running system and the
repository; where something is not built yet, the text says so.*
