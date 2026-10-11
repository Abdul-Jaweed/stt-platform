# STT Platform

**A provider-neutral speech-to-text platform.** One headless `/v1` API turns
audio into timestamped transcripts through swappable providers — a local
Faster-Whisper `large-v3-turbo` on CUDA, or a managed API — behind a single
internal contract. A separate React + TypeScript app consumes only that API,
so voice agents, scripts and the UI are all just clients.

Built phase by phase under strict TDD: **8 phases (0–7), all complete.**
Verified locally with **420 backend tests + 3 GPU tests + 48 frontend tests**,
plus real-stack end-to-end drivers that use the actual GPU, the actual
Temporal server and the actual object store.

![Transcript page — segments with word-level timestamps](docs/images/ui-transcript.png)

---

## Why it is shaped this way

* **Provider neutrality is the product.** Providers are interchangeable
  behind one contract (`transcribe` or `submit → poll → fetch`); capability
  differences are *discovered*, never assumed, and each capability is marked
  `documented` until our own live run proves it, then `verified`.
* **Durability is the default.** Job state lives in PostgreSQL, dispatch
  goes through a transactional outbox, execution runs as Temporal workflows
  with retries implemented *in the workflow* so every attempt writes a
  visible `RETRY_WAIT` transition. A killed worker does not lose a job.
* **Honesty is enforced.** A metric that could not be measured is `None`,
  never `0.0`. A job that failed says why and whether retrying is honest.
  Unverified reference audio is never scored.
* **Privacy is structural.** Audio is stored under opaque tenant-scoped keys,
  local-only audio is never silently routed to a cloud provider, and
  transcripts, audio bytes and secrets never reach the logs.

## Architecture

```mermaid
graph TB
    subgraph clients[Clients]
        UI["React SPA<br/>Vite + TypeScript"]
        AGENT["Voice agents"]
        CLI["API clients"]
    end

    subgraph apip["API process"]
        FAST["FastAPI · /v1"]
        AUTH["API-key auth<br/>tenant scope + quota"]
        OBS["/metrics · OpenTelemetry<br/>off by default"]
    end

    subgraph data[Data plane]
        PG[("PostgreSQL 16<br/>jobs · transitions · outbox<br/>provider_attempts · evaluations")]
        S3[("RustFS S3<br/>stt-audio · stt-artifacts<br/>opaque tenant-scoped keys")]
    end

    subgraph orch[Orchestration]
        TEMP["Temporal 1.29<br/>workflows · retries · timers"]
        OUTBOX["Transactional outbox<br/>at-least-once dispatch"]
    end

    subgraph wk[Worker process]
        WF["TranscriptionWorkflow<br/>bounded retries + backoff"]
        ACT["execute_attempt activity"]
    end

    subgraph prov[Provider adapters — one contract]
        FW["Faster-Whisper large-v3-turbo<br/>local CUDA"]
        DG["Deepgram nova-3"]
        EL["ElevenLabs Scribe v2"]
        SV["Sarvam saaras:v4"]
    end

    UI --> FAST
    AGENT --> FAST
    CLI --> FAST
    FAST --> AUTH
    AUTH --> PG
    FAST --> S3
    FAST --> OUTBOX --> TEMP
    TEMP --> WF --> ACT
    ACT --> S3
    ACT --> FW
    ACT --> DG
    ACT --> EL
    ACT --> SV
    ACT --> PG
    OBS -.-> FAST
```

Ports on a local run: API `8200` · Postgres `25432` · RustFS `29000` ·
Temporal `7233` (UI `28080`) · frontend dev server `5173` · MLflow `5000`.

## The life of a request

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant A as API
    participant S as S3 (RustFS)
    participant P as Postgres
    participant T as Temporal
    participant W as Worker
    participant V as Provider

    C->>A: POST /v1/transcriptions (audio file or presigned key)
    A->>A: validate content with ffprobe — never the extension
    A->>S: store the original under an opaque tenant-scoped key
    A->>P: job + transitions + outbox row — one commit
    A-->>C: 201 (short audio, inline) or 202 QUEUED + job id
    P-->>T: dispatcher starts the workflow (at-least-once)
    T->>W: execute_attempt
    W->>S: download the original
    W->>V: transcribe (word timestamps where the provider has them)
    V-->>W: segments, words, confidences
    W->>S: transcript.json (+ raw provider response when retained)
    W->>P: PROCESSING → SUCCEEDED, metrics, timings
    C->>A: GET /v1/transcriptions/{id}/result
    A-->>C: timestamped transcript (segments + words)
```

Short audio is transcribed inside the request; anything longer answers `202`
and is processed by the worker — the client polls the same URL either way.

## Job lifecycle

```mermaid
stateDiagram-v2
    [*] --> ACCEPTED
    ACCEPTED --> VALIDATING
    VALIDATING --> STORED
    VALIDATING --> FAILED
    STORED --> QUEUED
    STORED --> FAILED
    QUEUED --> PROCESSING
    QUEUED --> CANCELLED
    PROCESSING --> SUCCEEDED
    PROCESSING --> RETRY_WAIT
    PROCESSING --> FAILED
    PROCESSING --> CANCELLED
    RETRY_WAIT --> QUEUED
    RETRY_WAIT --> FAILED
    RETRY_WAIT --> CANCELLED
    FAILED --> QUEUED : retry
    SUCCEEDED --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
```

Every transition is a row, so the trail *is* the audit log. A real one, from
the Phase 7 chaos test — the worker was killed mid-transcription:

```
VALIDATING -> STORED -> QUEUED -> PROCESSING -> RETRY_WAIT -> QUEUED -> PROCESSING -> SUCCEEDED
```

## The UI

| | |
|---|---|
| ![Upload](docs/images/ui-upload.png) | ![Recording](docs/images/ui-recorder.png) |
| Upload a file, or record in the browser | Recording from the microphone, with timer and level |
| ![Job](docs/images/ui-job.png) | ![Evaluations](docs/images/ui-evaluations.png) |
| Job lifecycle tracker, provider, model, timings | Evaluation comparison, error rates as meters |

The transcript page (top of this README) renders segments with word-level
timestamps, copies the plain text in one click, and exports TXT/SRT/VTT.

**Recording in the browser.** The upload page has a *Record audio* tab: the
browser captures the microphone with `MediaRecorder`, shows a live timer and
input level, lets you preview and discard the take, and hands the finished clip
to the exact same upload path as a chosen file — one provider contract, no
special case. The microphone is released on stop, on discard, and on leaving
the page. Browser recordings are streaming WebM with **no duration header**;
the platform measures the real duration by decoding the stream instead of
rejecting the file (see `docs/ui.md`).

## Providers

| Provider | Kind | Models used | Word timestamps | Diarization | Status |
|---|---|---|---|---|---|
| Faster-Whisper (local) | CUDA, in-process | `large-v3-turbo` | yes | no | **verified** |
| Deepgram | managed, sync | `nova-3` | yes | yes | **verified** (1 live run) |
| ElevenLabs Scribe | managed, sync | `scribe_v2` | yes | yes | **verified** (1 live run) |
| Sarvam AI | managed, batch | `saaras:v4` | no (segment-level) | yes | **verified** (1 live run) |
| Amazon Transcribe | managed | — | — | — | deferred (no verified AWS setup) |

Managed providers register only when their API key is configured, so a missing
key means "not offered", never a 500. Live verification used free-tier keys,
STT models only, one 10 s clip each — see `docs/live-provider-verification.md`.

## Quickstart

Prerequisites: Windows 11 (or Linux), NVIDIA GPU with CUDA for the local
provider, `uv`, FFmpeg/ffprobe, Docker Desktop, Node 22+ for the frontend.

```bash
# 1. infrastructure: Postgres, RustFS (S3), Temporal, Temporal UI
cd infrastructure && docker compose up -d --wait

# 2. backend: schema, then API + worker in two shells
cd ../backend
uv sync
uv run alembic upgrade head
uv run uvicorn stt_platform.main:app --host 127.0.0.1 --port 8200
uv run python -m stt_platform.workers.transcription_worker

# 3. transcribe something
curl -s -F "file=@audio/sample.wav" http://127.0.0.1:8200/v1/transcriptions

# 4. frontend
cd ../frontend && npm install && npm run dev     # http://127.0.0.1:5173
```

Configuration lives in `backend/.env` (gitignored; `.env.example` documents
every variable and carries no values).

## API

All routes are under `/v1`. With `STT_AUTH_ENABLED=true`, everything except
the health probes requires an `X-API-Key` header.

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/transcriptions` | submit audio (multipart) or a presigned object key |
| `GET` | `/transcriptions/{id}` | job state, provider, model, attempts, failure |
| `GET` | `/transcriptions/{id}/result` | the timestamped transcript |
| `POST` | `/transcriptions/{id}/retry` | retry a failed job (honest about retryability) |
| `POST` | `/transcriptions/{id}/cancel` | cancel a queued or running job |
| `DELETE` | `/transcriptions/{id}` | delete job + objects + rows (tenant-scoped) |
| `POST` | `/transcriptions/uploads/presign` | presigned upload for large files |
| `GET` | `/providers` | capability matrix with provenance per capability |
| `POST` | `/evaluations` | run an evaluation against a reference dataset |
| `GET` | `/evaluations`, `/evaluations/{id}` | run history and comparison |
| `GET` | `/health/live`, `/health/ready` | probes (never authenticated) |

## Security and privacy

* **Tenant scope is server-assigned.** The tenant comes from the presented
  key, never from a request field; keys are compared in constant time; with
  auth on and no keys configured the API fails *closed*.
* **Authorization on read and write.** Another tenant's job is a `404`, not a
  `403` — existence is not confirmed.
* **Quotas** per tenant per minute at the API edge (in-process limiter — the
  documented single-node trade-off).
* **Retention and deletion.** `DELETE` removes audio, artifacts, the local
  copy and every row; `scripts/purge_expired.py` applies a retention window
  to terminal jobs only, with a non-destructive `--dry-run`.
* **Buckets are private** — asserted against the real RustFS: anonymous reads
  refused, no public ACL grants, no wildcard principals.
* **No secrets in git or the frontend** — enforced by tests, not habit.
* **Logs are redacted** — secrets, audio bytes and transcript text never
  appear; verified by tests on the real request path.

## Evaluation

`docs/evaluation.md` in full. Short version: a versioned reference dataset
with honest provenance, WER/CER cross-checked against `jiwer`, timestamp
quality metrics, and the operational picture (queue wait, RTF, retries) taken
from the real job records. Synthetic clips (ground truth by construction) are
scored; unverified human audio is never scored. Runs log to MLflow
best-effort — a dead tracking server yields `mlflow: {status: error}` and
never fails an evaluation.

Measured baseline (`large-v3-turbo`, 8 clips): **WER 0.100** micro / 0.118
macro, **CER 0.095**, **RTF 0.072**, word timestamps on 100% of clips.

## Operations

`docs/operations.md` (services, ports, config, routine tasks, backups,
capacity) and `docs/incident-recovery.md` (runbooks: power cut, Postgres
down, storage down, Temporal down, provider outage, stuck jobs, disk full,
credential exposure, deletion requests).

Measured capacity on one RTX 4060 with 10 s clips (`scripts/load_test.py`):

| Load | Wall | Throughput | p50 | p95 | Errors |
|---|---|---|---|---|---|
| 12 requests, concurrency 4 | 8.5 s | 1.41 req/s | 2.63 s | 2.66 s | 0 |
| 24 requests, concurrency 8 | 16.5 s | 1.45 req/s | 5.25 s | 5.45 s | 0 |

Throughput saturates near 1.45 req/s because one GPU serializes inference —
extra concurrency buys queue depth, not speed. Raise it with more GPUs or by
routing eligible tenants to a managed provider.

## Testing

```bash
cd backend
uv run pytest -q                     # 420 passed, 7 deselected (live-provider tests)
uv run pytest -m gpu -q              # 3 passed — real GPU evaluation
uv run ruff check src tests

cd ../frontend
npm test                             # 48 passed
npm run build
```

Real-stack drivers (they start processes, use the GPU and the compose stack):

```bash
python scripts/e2e_phase2.py         # durable async pipeline
python scripts/e2e_phase3_frontend.py# browser e2e for the UI
python scripts/e2e_phase4.py         # managed providers against a fake vendor
python scripts/e2e_phase5.py         # observability across two processes
python scripts/e2e_phase6.py         # evaluation against real MLflow
python scripts/e2e_phase7.py         # auth, tenancy, quota, chaos recovery, deletion
python scripts/load_test.py          # latency/throughput under concurrency
```

Tests that need the stack **skip** rather than fail when it is absent — check
for skips before reading a green run as proof.

## Repository layout

| Path | Purpose |
|---|---|
| `backend/` | FastAPI service + Temporal worker (uv, Python 3.13) |
| `frontend/` | React + TypeScript + Vite app (consumes only `/v1`) |
| `infrastructure/` | Docker Compose: PostgreSQL, RustFS, Temporal, Temporal UI |
| `scripts/` | e2e drivers, load harness, purge tool, corpus generator |
| `docs/` | architecture, per-phase reports, operations, incident recovery |
| `models/`, `.cache/`, `audio/`, `results/` | local artifacts — never committed |
| `app.py`, `benchmark_*.py` | pre-existing prototype scripts, preserved (ADR-006) |

No audio files are committed (ADR-007): test fixtures are synthesized at test
time, and the evaluation corpus is regenerated with
`scripts/make_eval_corpus.py`.

## Documentation

| Document | Contents |
|---|---|
| `docs/architecture.md` | architecture, ADR log, ports |
| `docs/api.md` | `/v1` walkthrough with verified example runs |
| `docs/reliability.md` | guarantees (outbox, retries, cancellation), known gaps |
| `docs/providers.md` | capability matrix, config, fidelity rules |
| `docs/live-provider-verification.md` | live vendor runs: numbers, defects, evidence |
| `docs/observability.md` | traces, logs, metric catalog, dashboard, alerts |
| `docs/evaluation.md` | datasets, metrics, normalization, MLflow, limits |
| `docs/ui.md` | frontend: pages, the browser recorder, streaming-WebM duration, design rules |
| `docs/operations.md` | running it: config, routine tasks, backups, capacity |
| `docs/incident-recovery.md` | what breaks, what the platform does, what you do |
| `docs/phase-{0..7}-report.md` | per-phase evidence and limitations |
| `docs/superpowers/plans/` | the plan each phase was built from |

## License

No license file yet — the author has not chosen one, so all rights are
reserved by default. Open an issue if you need a specific license.
