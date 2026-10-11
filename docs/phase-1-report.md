# Phase 1 report — Headless backend & local STT baseline

**Date:** 2026-10-11 · **Status: COMPLETE** — gates green, real-server
e2e verified on the RTX 4060.

## What shipped

A functional headless FastAPI backend that transcribes short audio with
Faster-Whisper `large-v3-turbo` on CUDA and returns canonical,
original-timeline-timestamped transcripts persisted in PostgreSQL.

| Task | Deliverable | Commit |
|---|---|---|
| 0 | Inspection, architecture/ADRs, phase plan; prototypes preserved | `63a3e15`, `5293918` |
| T1 | uv scaffold, `Settings` (`STT_` prefix), Windows CUDA DLL bootstrap, compose PostgreSQL 16 on 25432 | `aac024b` |
| T2 | Canonical schemas (transcript/job), transition map, timeline shift | `b8a421c` |
| T3 | ffprobe-based inspection + content-based validation (never trusts extensions) | `598173e` |
| T4 | Provider contract (`ProviderProtocol`, capabilities documented-vs-verified) + registry | `7a85258` |
| T5 | Faster-Whisper adapter (lazy cached CUDA engine, word timestamps, error classes) + GPU tests | `b253d14` |
| T6 | SQLAlchemy models, Alembic migration `658d52babc7b`, repository with transition enforcement | `3f9bea9` |
| T7 | Job service: idempotent creation, scope-safe lifecycle, sync processing, retry | `be2f0e4` |
| T8 | `/v1` API: transcriptions, providers, health, pinned error mapping, OpenAPI | `5288cd4` |
| T9 | Wire-up, docs (`api.md`, `reliability.md`), e2e run, this report | this commit |

Key files: `backend/src/stt_platform/{main.py, core/, domain/, schemas/,
audio/, providers/, persistence/, services/, api/}`;
`backend/migrations/`; tests `backend/tests/{unit,integration,gpu}/`.
API docs: `docs/api.md`; reliability semantics: `docs/reliability.md`.

## Dependencies

Runtime: fastapi 0.143.0, uvicorn[standard] 0.54.0, pydantic 2.14.0,
pydantic-settings, sqlalchemy ≥2.0.36, alembic, psycopg[binary] 3,
python-multipart 0.0.32, faster-whisper 1.2.1, ctranslate2 4.8.2,
onnxruntime 1.31.0, nvidia-cublas-cu12 12.9.2.10, nvidia-cudnn-cu12
9.27.0.42, **av 18.1.0 (pinned — see incidents)**.
Dev: pytest, httpx, testcontainers[postgres]. Locked with `uv.lock`.

## Commands

```bash
cd backend && uv sync && uv run alembic upgrade head
uv run pytest -q          # unit + integration
uv run pytest -m gpu -q   # real CUDA model
uv run uvicorn stt_platform.main:app --host 127.0.0.1 --port 8200
curl -s -F "file=@audio/sample.wav" http://127.0.0.1:8200/v1/transcriptions
```

## Actual test results (this machine, this date)

- `uv run pytest -q` → **97 passed, 2 deselected** in 10.40 s
  (56 unit + 41 integration; integration runs an ephemeral
  testcontainers PostgreSQL).
- `uv run pytest -m gpu -q` → **2 passed, 97 deselected** in 4.66 s.
- GPU smoke (`results/phase1_gpu_smoke.json`): 10 s sample → 1 segment,
  26 words, language `en` (prob 0.996), processing 0.919 s → **RTF
  0.0919** (elapsed 3.234 s incl. model load).
- **Real-server e2e** (uvicorn + compose PG + real model):
  - `GET /v1/health/live` → 200 in 1 s; `ready` → `{"status":"ready"}`
  - `GET /v1/providers` → 200, `faster-whisper` with capabilities
  - `POST /v1/transcriptions` (`audio/sample.wav`, `language=en`,
    `Idempotency-Key: e2e-demo-1`) → **201** in 3 s wall,
    `Location: /v1/transcriptions/aabecb8f-8eca-4734-81de-0af47296ff7c`
  - job `SUCCEEDED`, `attempt_count=1`, `model=large-v3-turbo`,
    audio probed `wav / pcm_s16le / 16000 Hz / mono / 10.0 s / 320044 B`
  - result: `language=en`, `timestamp_granularity=word`, 1 segment
    (0.59–9.89 s), 26 words with per-word probabilities,
    `processing_duration_s=0.776` → **RTF ≈ 0.078**
  - transcript (`result.json`, full): *"Hello, I'm Abdul Javid, AI
    Engineer at Home and Managed, and currently I'm working multiple
    projects, projects like a mass platform, basically memory as a
    service."*
  - raw bodies: `results/phase1_e2e/*.json` (gitignored artifact)

## Decisions (with rationale)

1. **Sync execution, stable URL contract** — Phase 1 runs the lifecycle
   inline; jobs are terminal when POST returns. The `/v1` contract is
   unchanged when Temporal arrives (only status becomes non-terminal).
2. **Two-layer validation** — HTTP pre-flight rejects bad media with a
   clean 4xx and *no job row* (sync UX), while the job-layer
   `VALIDATING` state re-checks as defense in depth and records
   failures as `FAILED` jobs.
3. **`av==18.1.0` pinned** — the day-old av 19.0.1 changes `av.open()`
   (`metadata_errors` kwarg) and breaks faster-whisper 1.2.1 decoding;
   18.1.0 is the proven working version from Phase 0.
4. **Structural scope isolation** — every repository call is
   scope-filtered; clients never supply a scope in Phase 1
   ("never trust client tenant IDs" enforced by construction).
5. **Uploads retained** under `.data/audio/` because retry requires the
   original bytes; object storage with opaque keys is Phase 2.
6. **Cancel → explicit 501** until the async pipeline makes cancellation
   meaningful; the route exists so the contract is stable.
7. **`capability_sources`** distinguishes *documented* from *verified*
   capabilities — word timestamps are verified (integration + GPU
   tests), formats are documented.

## Incidents found and fixed during the phase

- `.gitignore` `audio/` silently excluded `src/stt_platform/audio/`;
  anchored all ignore rules to the repo root (T3, amended commit).
- **av 19.0.1 breakage** (above) — caught by the GPU test (T5).
- **Test-harness deadlock**: an unclosed SQLAlchemy `Session` left a
  connection idle-in-transaction; the next test's `drop_all` waited on
  its locks forever. Root-caused with a `faulthandler_timeout` stack
  dump; fixed by closing all sessions in fixture teardown (T7).
- Migrations test became order-dependent once a new integration file
  landed; it now starts from a clean slate (T7).
- `str(engine.url)` masks the password as `***` — API tests got auth
  failures; pass the raw URL instead (T8).
- Readiness test against a dead port hung 130 s (no connect timeout);
  `?connect_timeout=1` in the dead-DB URL (T8).
- Starlette re-raises server errors after producing the 500 response;
  the 500-body test uses `raise_server_exceptions=False` (T8).

## Limitations (honest)

- Synchronous path only (≤ `STT_MAX_SYNC_DURATION_S`, default 180 s);
  long audio awaits Phase 2.
- `cancel` → 501; no automatic retries, backoff, or circuit breakers.
- An unexpected non-domain exception leaves the job in `PROCESSING`
  (500 to client; no compensating write yet — Phase 2 sweeper).
- Idempotency check-then-insert is not race-safe under concurrent
  identical submissions (unique index is the backstop; Phase 2 fixes).
- Uploads have no retention/lifecycle policy; a replayed idempotent
  upload leaves an extra file copy on disk.
- `JobRecord` exposes the local `audio_path` (single-node acceptable;
  replaced by opaque storage keys in Phase 2).
- Readiness checks PostgreSQL only; CUDA/provider health is visible via
  `/v1/providers`, not `/v1/health/ready`.
- One in-flight transcription per process (single GPU); concurrency
  classes are Phase 2.
- Housekeeping from Phase 0 still open: duplicate `large-v3-turbo` copy
  in the HF hub cache (`models--mobiusla…`), harmless, uncommitted.

## Next phase (2) preview

Temporal workflows + transactional outbox, long-audio chunking
(`domain/timeline.shift_segments` is already the offset basis), RustFS/S3
object storage with opaque keys and retention, cancel, race-safe
idempotency with key expiry, model warm-up at startup, containerized
backend image.
