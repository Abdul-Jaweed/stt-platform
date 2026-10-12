# Architecture diagrams

Interactive, self-contained HTML diagrams for the provider-neutral STT platform, generated
with the `archify` skill from the repository at revision
`2b44b8d79891e835f41ff49d64c5dfa8f197081c` (`git@github.com:Abdul-Jaweed/stt-platform.git`).

Every component in these diagrams cites the repository path and line range it was read from.
Validation checks that each citation exists at the pinned revision — it proves the drawing is
well-formed and evidence-backed, not that the platform runs.

## Files

| File | Type | Status |
|---|---|---|
| `stt-platform-architecture.html` | architecture | delivered — all gates pass |
| `stt-platform-architecture.architecture.json` | typed JSON source | delivered |
| `transcription-job-lifecycle.html` | lifecycle | delivered — all gates pass |
| `transcription-job-lifecycle.lifecycle.json` | typed JSON source | delivered |
| `failure-recovery-workflow.html` | workflow | **not delivered** — see [Known gaps](#known-gaps) |
| `*.finalize.json`, `*.browser-check.json`, `*.delivery.json` | machine receipts | evidence for the rows above |

## How to open

Open either `.html` file directly in a browser — no server, no network, no build step. Each
file is fully self-contained (inline SVG, inline CSS, inline JS) and carries a light/dark
theme toggle plus guided views, search, and focus controls. A copy can be handed to anyone
or committed as-is.

## What the architecture diagram shows

**Boundaries (grouped containers)**

- **Client layer** — the React + TS + Vite console, programmatic API clients, and voice-agent
  consumers. The console talks only to the backend `/v1` API; it holds no provider SDK and no
  secret. Its session is an `HttpOnly`, `SameSite=Lax` cookie whose tenant the server assigns.
- **Tenant isolation** (security group) — tenant scope is derived from the presented API key,
  never from a client-supplied field, and is enforced on both creation and retrieval.
- **Headless API / control plane** — FastAPI, versioned `/v1` + OpenAPI, auth, request
  validation, idempotency, job management, provider selection, status and result retrieval.
  The backend is headless: the console is a separate deployment.
- **Audio ingestion** — multipart upload through the API and one-shot presigned PUT straight
  to object storage, content/codec/duration checks, ffmpeg/ffprobe probing. The original audio
  is preserved; conversion happens only when required, and every timestamp refers to the
  original timeline.
- **Durable orchestration** — Temporal workflows and activities, retries, provider polling,
  cancellation, CPU-bound audio work, GPU-bound local inference, and outbox-driven dispatch.
- **Provider abstraction** — one internal contract with separate adapters: local
  Faster-Whisper `large-v3-turbo` on the NVIDIA GPU (inside a "local-only — never falls back
  to cloud" boundary), plus Sarvam AI, Deepgram, and ElevenLabs Scribe as managed adapters.
  Normalized provider responses flow into transcript normalization.
- **Transcript normalization** — canonical Pydantic schema, full text, timestamped segments,
  optional word timestamps and speaker metadata, language/provider/model metadata, warnings,
  and SRT/VTT export.
- **Persistence** — PostgreSQL (jobs, transitions, idempotency records, provider attempts,
  transcript metadata, evaluation metadata), RustFS/S3 object storage (opaque, tenant-scoped
  keys), Redis (planned cache/coordination), and Temporal's own durable workflow state.
- **Observability & evaluation** — OpenTelemetry for operations, MLflow for experiments,
  versioned evaluation datasets with human-verified references, WER/CER, timestamp accuracy,
  RTF, and Grafana-compatible dashboards and alerts.
- **Security & trust** — client→backend, backend→provider, tenant isolation, secrets, storage
  access control, retention/deletion (objects first, then rows), and privacy-aware routing.

**Primary flow** — client → FastAPI request → auth/authorization/validation → audio upload to
durable object storage → idempotent job creation → Temporal workflow → inspection →
provider selection → local or managed adapter → transcript normalization → PostgreSQL result
metadata + object-store artifacts → job completion → API result retrieval → console display
and download.

**Asynchronous response** — the API answers a job it cannot finish inline with HTTP `202
Accepted` and a job id; the request path ends at the outbox row and the worker owns
everything after it.

**Failure and recovery** — retryable provider errors, timeouts, throttling, worker crashes,
and exhausted retries are represented by the retry contract: bounded backoff with jitter,
a `RETRY_WAIT` transition for every attempt, provider polling that survives a worker crash,
and cancellation that ends the workflow rather than retrying.

## Planned vs implemented

Implemented and evidenced in the codebase: the FastAPI control plane, UI auth, audio
validation, the outbox and workflow/worker execution, all four provider adapters, transcript
normalization, PostgreSQL and RustFS persistence, retention/deletion, evaluation + MLflow,
and the observability instrumentation (off by default).

Marked **planned — not implemented** on the diagram, with sources:

- **Amazon Transcribe** and **Amazon Bedrock Data Automation** — specified (spec Phase 4 and
  an optional evaluation), not written.
- **Redis** — today the per-tenant limiter is an in-process sliding window; Redis is not the
  authoritative job database and is not deployed.
- **Local chunking of long audio** — the offset-shift helper exists, but long audio relies on
  provider limits.
- **Grafana/Prometheus** — shipped as dashboard and alert artifacts for an operator-owned
  stack, not deployed by this repository.

## Validation evidence

Both delivered diagrams passed all four `finalize` gates (`validate`, `deliver`, strict
`check`, real-browser `browser-check`) with zero errors and zero warnings.

| Diagram | Specification SHA-256 | Artifact SHA-256 | Bytes |
|---|---|---|---|
| architecture | `3caceaac1f022d2d…` | `bc1fdce3fe6eddbb…` | 817,286 |
| lifecycle | `8313e74139d4dab6…` | `71a742ab24933e86…` | 771,948 |

Full hashes are in the `.finalize.json` receipts. Browser evidence additionally measured
containment at 1440×900, 1600×1000, 1920×1080, and 2048×1320 in both themes, plus the
READ and Still runtime states. Optional `visual-check` captures (four viewport PNGs and an
HTML contact sheet per diagram) live under
`.archify/architecture-stt-platform-20261012-051305/visual-check-*/`.

**No perceptual visual review was performed** — the captures are available for a human
reviewer; the automated checks are the claims made here.

## Known gaps

- **`failure-recovery-workflow.html` is not delivered.** The candidate
  (`failure-recovery-workflow.workflow.json` in this directory)
  is authored and its citations validate, but its layout gate does not pass: the retry loop's
  return edge (`backoff → execute`) and the retry entry edge (`verdict → retry_wait`) cross the
  forward edges in every layout tried, and the bounded repair budget was exhausted. The
  failure/recovery semantics are still covered: the architecture diagram's cards state the
  retry/cancellation contract, and the lifecycle diagram shows the `RETRY_WAIT`, `FAILED`, and
  `CANCELLED` paths with their sources.
- The architecture diagram deliberately omits an edge for the presigned browser→storage
  upload path (it crosses the whole canvas); the fact is carried by the console tag and a
  trust card instead.
