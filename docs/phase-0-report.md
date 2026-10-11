# Phase 0 Report — Repository Inspection and Architecture Confirmation

Date: 2026-10-11. Method: direct inspection of `I:\voice-agent-course`
(with `local-stt` as project root), its environments, caches, scripts,
model files, Docker state, and live hardware; evidence commands were run
during this phase and their observed outputs are cited inline.

## 1. Repository structure (as found, annotated)

```text
I:\voice-agent-course\                       # course workspace (NOT the repo root)
├── .env                      # contains SARVAM_API_KEY (value not read/recorded)
├── .uv-python\               # uv-managed CPython 3.12.14 + cpython-3.12 (on I:)
├── .venv\                    # working Faster-Whisper env — Python 3.13, 51 pkgs
├── .venv-nemo\ → (local-stt) # see below
├── benchmark_stt.py          # early CPU benchmark (base/int8) — superseded
├── evaluate_stt.py           # WER/CER via jiwer — seed for evaluation module
├── generate_audio.py         # synthetic tone fixture generator — pattern reused in tests
├── recordings\synthetic_audio.wav
├── requirements.txt          # faster-whisper, soundfile, numpy, jiwer
├── stt_engine.py             # first adapter-shaped engine (dataclass result) — reference
├── transcribe_file.py        # CLI wrapper around stt_engine — superseded
├── third_party\whisper.cpp\  # full whisper.cpp source clone (out of scope)
└── local-stt\                # ← PROJECT ROOT (ADR-001)
    ├── .cache\{uv,nemo,torch}       # uv cache used; nemo/torch caches empty
    ├── .venv-nemo\                  # NeMo env — Python 3.12, 143 pkgs (untouched)
    ├── app.py                       # evolved prototype: batch/live/api modes, CUDA bootstrap
    ├── app_before_overlap.py        # earlier prototype variant
    ├── audio\sample.wav             # 10.0 s, 16 kHz mono — owner's voice (never commit)
    ├── benchmark_models.py          # turdo/distil benchmark → results/, download_root=models/faster-whisper
    ├── benchmark_parakeet.py        # NeMo parakeet benchmark (needs .venv-nemo)
    ├── local_paths.py               # cache redirection to I: drive — strategy adopted
    ├── models\faster-whisper\       # CT2 models: large-v3-turbo + distil-large-v3 (3.0 GB)
    ├── models\huggingface\          # HF hub cache: parakeet + turbo duplicate (2.4+ GB)
    ├── models\whisper.cpp\          # ggml-large-v3-turbo.bin (1.6 GB) — out of scope
    ├── output\                      # empty
    ├── record.py                    # mic recorder (created audio/sample.wav)
    ├── requirements.txt             # faster-whisper, sounddevice, numpy, fastapi, uvicorn[standard], python-multipart, scipy, soundfile
    ├── results\                     # faster_whisper_benchmark.{json,csv}, parakeet.json
    ├── test_vad.py                  # VAD on/off comparison harness
    └── third_party\                 # empty
```

Neither this directory nor its parent was a git repository (verified);
Phase 0 initializes one here (ADR-001). Git identity is configured
(`Abdul-Jaweed <jdgaming7320@gmail.com>`); no remote exists.

## 2. Environments

| Env | Python | Packages | Role | Verdict |
|---|---|---|---|---|
| `I:\voice-agent-course\.venv` | 3.13 (uv) | 51 | **Working Faster-Whisper + FastAPI stack**: faster-whisper 1.2.1, ctranslate2 4.8.2, av 18.1.0, onnxruntime 1.31.0, fastapi 0.143.0, uvicorn 0.54.0, pydantic 2.14.0, sounddevice 0.5.6, nvidia-cublas-cu12 12.9.2.10, nvidia-cudnn-cu12 9.27.0.42 | Proven on this GPU; backend mirrors these versions in its own env |
| `local-stt\.venv-nemo` | 3.12 (I: `.uv-python`) | 143 | NeMo/torch experiments: nemo-toolkit 3.0.0, torch 2.11.0+cu130, transformers 5.19.0 | Leave untouched; not required by the platform |

`uv` 0.12.18 installed; managed pythons available (3.13.15, 3.12.10 in
user dir; 3.12.x on I:). Python 3.11.9 system install exists but is not
used by this project. No consolidated env is required (ADR-002).

## 3. Model caches and evidence

- `models/faster-whisper`: `models--mobiuslabsgmbh--faster-whisper-large-v3-turbo`
  and `models--Systran--faster-distil-whisper-large-v3` (complete CT2
  snapshots: config.json, model.bin, tokenizer, vocabulary).
- `models/huggingface/hub`: `models--nvidia--parakeet-tdt-0.6b-v2`; plus a
  **duplicate** turbo snapshot (~1.6 GB) created by the Phase 0 validation
  run because it loaded without `download_root` (default cache). Kept in
  place (harmless, regenerable); canonical cache remains
  `models/faster-whisper` via explicit `download_root` (ADR-003). Optional
  cleanup: delete `models/huggingface/hub/models--mobiuslabsgmbh--*`.
- `models/whisper.cpp/ggml-large-v3-turbo.bin` (1.6 GB): whisper.cpp
  experiment asset; out of scope, preserved.
- Sizes: models total ≈ 6.8 GB (3.0 + 2.4 + 1.6). I: has 244 GB free.

**Verification runs (CUDA, RTX 4060 8188 MiB, driver 610.62, cc 8.9):**

| Run | Result |
|---|---|
| `ctranslate2.get_cuda_device_count()` | `1` |
| First smoke load + 10 s clip (cold, incl. HF download) | load 168.8 s, infer 2.4 s, 1 segment / 26 words, lang `en` |
| Warm load from `models/faster-whisper` + same clip | **load 4.7 s, infer 0.9 s**, 26 words |
| Owner's earlier benchmark (`results/faster_whisper_benchmark.json`) | load 169.3 s (cold), inference 1.938 s → RTF 0.1938, word-level timestamps with probabilities |

Conclusion: the working CUDA configuration and cached weights are real and
reusable; the new adapter only needs explicit `download_root` to avoid
cold re-downloads.

## 4. Existing components worth reusing

| Source | Component | Reuse in |
|---|---|---|
| `app.py` | Windows CUDA DLL bootstrap (add_dll_directory before ct2 import) | `core` bootstrap module (ADR-009) |
| `app.py` | Load-once model pattern; transcribe w/ word timestamps; `offset` applied to segment/word times | Faster-Whisper adapter; chunk-offset restoration tests |
| `app.py` | Live-mode chunk overlap (0.8 s) + word-level dedup boundary logic | reference design for Phase 2 long-audio |
| `app.py` | FastAPI prototype (upload limits, 400/413 handling, decode via PyAV) | API patterns (superseded formally, not deleted) |
| `local_paths.py` | Cache redirection strategy (HF, torch, NeMo, uv on I:) | `core/config.py` settings |
| `stt_engine.py` | Adapter-shaped class + audio sanity checks (soundfile) | provider contract shape |
| `evaluate_stt.py` | jiwer WER/CER | Phase 6 evaluation module (dep: jiwer) |
| `generate_audio.py` | synthetic WAV generation | committed test-fixture pattern (ADR-007) |
| `benchmark_models.py` | benchmark harness → results JSON/CSV, `download_root` usage | corrected to canonical cache; seeds Phase 6 comparisons |
| `results/*` | RTF/timestamp evidence for large-v3-turbo | baseline numbers for evaluation |

## 5. Tooling and infrastructure state (verified)

- Docker engine 29.8.2 running; Compose v5.5.1. **No containers running.**
  Stopped foreign stacks exist (all `Exited (255)`, killed by engine
  restart): mlfast (PG 15432, Redis 6379, RustFS 19000/19001, frontend,
  observability), enterprise-ai-support (PG 5432, MLflow 5000), a
  standalone MLflow stack (PG 5432, minio/rustfs). No listeners on any
  of those ports right now.
- Already-pulled images we can reuse: `postgres:16`, `postgres:15`,
  `timescale/timescaledb:pg16`, `redis:7-alpine`, `rustfs/rustfs:1.0.1`,
  `ghcr.io/mlflow/mlflow:latest`. Temporal images are **not** pulled yet
  (Phase 2 prerequisite).
- FFmpeg/ffprobe 8.0, Node v22.20.0, npm 11.19.1, uv 0.12.18, git
  (identity configured). No project-level remote.

## 6. Secrets and configuration

- `I:\voice-agent-course\.env` (course root) holds a `SARVAM_API_KEY`.
  Value was not read into any log. Phase 4 will source it via environment;
  it will not enter the repository.
- Deepgram and ElevenLabs credentials: none found → Phase 4 prerequisite
  (owner must provide keys; "configured independently" acceptance item).
- No `.env` exists inside the new repo; `.env.example` templates only.

## 7. Risks, limitations, and notes

1. **Cold-load trap (solved):** loading without `download_root` duplicates
   the 1.6 GB model into the HF default cache and costs ~169 s. The
   adapter must always pass the canonical cache path (covered by ADR-003
   and pinned in Phase 1 tests).
2. **HF cache symlinks degraded:** Developer Mode is off, so huggingface_hub
   copies files instead of symlinking (disk duplication; logged and
   silenced via `HF_HUB_DISABLE_SYMLINKS_WARNING=1` in app config).
3. **Two liveness periods collide by design:** other projects' containers
   are stopped; this project's 2xxxx port block avoids all known clashes
   even if they are restarted (ADR-005).
4. **Environment split is real:** `benchmark_parakeet.py` needs
   `.venv-nemo`; everything phase-1 uses the new `backend/.venv`. Do not
   consolidate (ADR-002).
5. **Windows path/process quirks** to encode in tooling: native tools need
   `I:/...`-style paths; testcontainers needs `TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1`;
   background `docker compose up -d` should be followed by a health check.
6. **`httpx2` 2.13.1** appears in both existing envs (unusual rename/fork;
   not the official `httpx`). The backend will depend on standard packages
   resolved by uv; this oddity is not mirrored.
7. **Legacy prototype port 8000** stays free for `app.py`; the new backend
   binds 8200.
8. **whisper.cpp and NeMo/parakeet tracks** are preserved but out of scope;
   no work should delete or "clean" their assets.

## 8. Decisions

See `docs/architecture.md` §14 (ADR-001 … ADR-010): repo root, backend
runtime (3.13), canonical model caches, sync SQLAlchemy + psycopg3, own
compose with 2xxxx ports, prototypes preserved, no committed audio,
testcontainers for tests, CUDA bootstrap module, uv exclusivity.

## 9. Environment & infrastructure prerequisites

| Prerequisite | Status |
|---|---|
| GPU + driver 610.62, CUDA via pip wheels | ✅ verified (cuda_devices=1) |
| Cached large-v3-turbo (warm load 4.7 s) | ✅ verified |
| FFmpeg/ffprobe 8.0 | ✅ |
| Docker engine + Compose | ✅ running |
| `postgres:16` image | ✅ pulled |
| Node 22 / npm 11 (Phase 3) | ✅ |
| Temporal images (Phase 2) | ⏳ not pulled yet |
| Managed-provider keys (Phase 4) | ⏳ Sarvam present; Deepgram/ElevenLabs needed |
| Git remote (optional, for push) | ⏳ none configured |

## 10. Phase 1 plan (summary)

Full task-by-task plan:
`docs/superpowers/plans/2026-10-11-phase-1-backend-local-stt.md`.

1. **Scaffold** — `backend/` uv project (Python 3.13, pinned deps), package
   layout, settings/config with canonical cache paths, compose PostgreSQL
   (25432), pytest bootstrap.
2. **Canonical schemas** — transcript + job Pydantic models with fidelity
   rules (segment/word separation, original-timeline bounds). *(TDD)*
3. **Audio inspection/validation** — ffprobe wrapper, content-based
   validation (never trust extensions), limits, safe rejection. *(TDD)*
4. **Provider contract** — base protocol, capability model (documented vs
   verified), error normalization, registry. *(TDD)*
5. **Faster-Whisper adapter** — cached-model loading, word timestamps,
   offset restoration, capabilities; GPU integration test on
   `audio/sample.wav`. *(TDD)*
6. **Persistence** — SQLAlchemy models, Alembic migration, repository;
   ephemeral PG tests. *(TDD)*
7. **Services** — job creation with `Idempotency-Key` + fingerprint,
   synchronous short-audio processing with full state-transition trail,
   normalized failures. *(TDD)*
8. **API `/v1`** — transcriptions (submit/get/result/retry), providers,
   health live/ready; structured errors; OpenAPI checks. *(TDD)*
9. **Wire-up & report** — runnable app, README run commands, full suite
   (incl. one real GPU transcription), phase report.

Acceptance for Phase 1: a functional headless backend that transcribes
supported audio with Faster-Whisper `large-v3-turbo` on CUDA, persists
jobs/transcripts in PostgreSQL, returns canonical original-timeline
timestamps, and passes its unit + integration tests.
