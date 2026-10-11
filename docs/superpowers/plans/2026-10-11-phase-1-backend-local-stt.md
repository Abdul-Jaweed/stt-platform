# Phase 1 — Headless Backend & Local STT Baseline — Implementation Plan

> **Status: COMPLETE (2026-10-11).** Tasks 1–9 executed under TDD; gates green (97 tests + 2 GPU tests); real-server e2e verified. See `docs/phase-1-report.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A functional headless FastAPI backend that transcribes short audio with Faster-Whisper `large-v3-turbo` on CUDA and returns canonical, original-timeline-timestamped transcripts persisted in PostgreSQL.

**Architecture:** Modular monolith at `backend/src/stt_platform`: thin versioned API → job service → provider contract (Faster-Whisper adapter) + audio validation + SQLAlchemy persistence. Windows CUDA DLL bootstrap runs before any ctranslate2 import. All caches/artifacts on the I: drive.

**Tech Stack:** Python 3.13 (uv-managed), FastAPI 0.143, Pydantic 2.14, SQLAlchemy 2.x + psycopg 3 + Alembic, faster-whisper 1.2.1 + ctranslate2 4.8.2 (CUDA), ffprobe 8.0, PostgreSQL 16 (Docker, port 25432), pytest + testcontainers.

**Spec:** `docs/phase-0-report.md` (inspection + evidence), `docs/architecture.md` (ADRs), master specification §7–§12, §17–§21.

## Global Constraints

- uv is the **only** Python workflow (`uv lock`, `uv sync`, `uv run`); no pip/conda/poetry. `UV_CACHE_DIR=I:/voice-agent-course/local-stt/.cache/uv` (I: drive, reuse existing wheels).
- Python 3.13 (`backend/.python-version` = `3.13`).
- Pins: `faster-whisper==1.2.1`, `ctranslate2` (transitive ==4.8.2), `onnxruntime==1.31.0`, `nvidia-cublas-cu12==12.9.2.10`, `nvidia-cudnn-cu12==9.27.0.42`, `fastapi==0.143.0`, `uvicorn[standard]==0.54.0`, `pydantic==2.14.0`, `python-multipart==0.0.32`.
- Model cache: `models/faster-whisper` via explicit `download_root`; `HF_HOME` = `models/huggingface`; never user-level caches (warm load verified 4.7 s — never re-download).
- PostgreSQL 16 @ `127.0.0.1:25432` (db/user `stt`); backend dev port **8200**.
- Never commit audio, model weights, credentials, caches.
- **TDD iron law:** no production code without a witnessed failing test first (config files and generated fixtures excepted).
- Timestamps refer to the original audio timeline; never fabricate word timings/confidence/language/diarization; `timestamp_granularity == "segment"` ⇒ **no** word timings.
- Windows CUDA DLL registration (`os.add_dll_directory` for `nvidia/{cublas,cudnn}/bin` in `sys.prefix`) must execute **before** importing `faster_whisper`/`ctranslate2` in every process.
- Handlers thin; provider logic confined to adapters; dependency injection for settings/providers/persistence.
- Run tests with `cd backend && uv run pytest` (GPU tests opt-in: `uv run pytest -m gpu`).

## Review Focus

1. Lying/corrupt media — a text file named `.wav`, an empty file, random bytes: clean 4xx (`UNSUPPORTED_MEDIA`), never a 500 or a crash. *(pinned in Task 3/8 tests)*
2. Timestamps out of bounds / non-monotonic from the engine: schema validation rejects (or clamps only with recorded warning); a bad transcript is never persisted silently. *(Task 2, Task 5)*
3. Duplicate submission with same `Idempotency-Key` + same fingerprint ⇒ same job, no duplicate processing; same key + different fingerprint ⇒ 409. *(Task 7)*
4. Unknown provider id / language handling: structured 422 with `PROVIDER_NOT_FOUND`, not a crash. *(Task 4, Task 8)*
5. Oversized upload ⇒ 413 without buffering the whole body. *(Task 3/Task 8)*

---

### Task 1: Backend scaffold, settings, compose PostgreSQL

**Files:**
- Create: `backend/pyproject.toml`, `backend/.python-version`, `backend/.env.example`, `backend/README.md`
- Create: `backend/src/stt_platform/__init__.py`, `backend/src/stt_platform/core/__init__.py`, `backend/src/stt_platform/core/config.py`, `backend/src/stt_platform/core/cuda.py`
- Create: `backend/tests/__init__.py`, `backend/tests/conftest.py`, `backend/tests/unit/test_config.py`, `backend/tests/unit/test_cuda.py`
- Create: `infrastructure/docker-compose.yml`
- Modify: `.gitignore` (already covers `backend/.venv`, `.data/`)

**Interfaces (produces for all later tasks):**
- `get_settings() -> Settings` (cached) with fields: `database_url`, `repo_root`, `model_dir`, `hf_home`, `temp_dir`, `audio_store_dir`, `whisper_model`, `device`, `compute_type`, `max_upload_bytes`, `max_sync_duration_s`, `sync_duration_tolerance_s`; method `apply_runtime_env()` (sets `HF_HOME`, `HF_HUB_CACHE`, `HF_HUB_DISABLE_SYMLINKS_WARNING`, `UV_CACHE_DIR`).
- `core.cuda.register_cuda_dll_dirs() -> None` — import-time side effect, idempotent.

- [x] **Step 1: pyproject.toml** (exact)

```toml
[project]
name = "stt-platform-backend"
version = "0.1.0"
description = "Provider-neutral Speech-to-Text platform backend"
requires-python = ">=3.13,<3.14"
dependencies = [
  "fastapi==0.143.0",
  "uvicorn[standard]==0.54.0",
  "pydantic==2.14.0",
  "pydantic-settings>=2.6",
  "sqlalchemy>=2.0.36,<2.1",
  "alembic>=1.14",
  "psycopg[binary]>=3.2",
  "python-multipart==0.0.32",
  "faster-whisper==1.2.1",
  "onnxruntime==1.31.0",
  "nvidia-cublas-cu12==12.9.2.10",
  "nvidia-cudnn-cu12==9.27.0.42",
]

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.28", "testcontainers[postgres]>=4.9"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/stt_platform"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not gpu'"
markers = ["gpu: requires CUDA GPU and cached model"]
```

- [x] **Step 2:** `export UV_CACHE_DIR=I:/voice-agent-course/local-stt/.cache/uv && cd backend && uv lock && uv sync` — expects lock created, env synced (wheel cache hits from `.cache/uv` avoid ~1.5 GB of downloads).

- [x] **Step 3: core/cuda.py** (exact)

```python
"""Windows CUDA DLL registration. Import BEFORE faster_whisper/ctranslate2."""
import os, sys
from pathlib import Path

_DLL_HANDLES: list = []

def register_cuda_dll_dirs() -> None:
    if os.name != "nt" or _DLL_HANDLES:
        return
    nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for relative in ("cublas/bin", "cudnn/bin"):
        dll_dir = nvidia_root / relative
        if dll_dir.is_dir():
            _DLL_HANDLES.append(os.add_dll_directory(str(dll_dir)))
            os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")

register_cuda_dll_dirs()
```

- [x] **Step 4: core/config.py** — `Settings(BaseSettings)` with `env_prefix="STT_"`, `env_file=".env"`, defaults: `repo_root = Path(__file__).resolve().parents[4]` (config.py → core → stt_platform → src → backend → local-stt); `model_dir = repo_root/"models"/"faster-whisper"`; `hf_home = repo_root/"models"/"huggingface"`; `temp_dir = repo_root/".data"/"tmp"`; `audio_store_dir = repo_root/".data"/"audio"`; `database_url = "postgresql+psycopg://stt:stt@127.0.0.1:25432/stt"`; `whisper_model = "large-v3-turbo"`; `device="cuda"`; `compute_type="float16"`; `max_upload_bytes = 100*1024*1024`; `max_sync_duration_s = 180.0`; `sync_duration_tolerance_s = 0.5`. `apply_runtime_env()` sets the cache env vars and `mkdir(parents=True, exist_ok=True)` for `temp_dir`/`audio_store_dir`. `@lru_cache get_settings()`.

- [x] **Step 5: tests first** — `test_config.py`: default `model_dir` points inside repo `models/faster-whisper`; `STT_DATABASE_URL` env override wins; `apply_runtime_env()` sets `HF_HOME` to `hf_home`. `test_cuda.py`: on `nt`, after import, PATH contains `cublas` bin dir when present; calling `register_cuda_dll_dirs()` twice does not duplicate. Run: `uv run pytest tests/unit/test_config.py tests/unit/test_cuda.py -v` → FAIL (module missing) → implement cuda/config → PASS.

- [x] **Step 6: compose** (exact) `infrastructure/docker-compose.yml`

```yaml
name: stt-platform
services:
  postgres:
    image: postgres:16
    container_name: stt-postgres
    environment:
      POSTGRES_DB: stt
      POSTGRES_USER: stt
      POSTGRES_PASSWORD: stt
    ports: ["25432:5432"]
    volumes: ["../.data/postgres:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U stt -d stt"]
      interval: 5s
      timeout: 3s
      retries: 10
```

Start + health: `cd infrastructure && docker compose up -d postgres && docker compose ps` (wait for `healthy`). Connect check: `cd backend && uv run python -c "import psycopg; psycopg.connect('postgresql://stt:stt@127.0.0.1:25432/stt'); print('pg ok')"`.

- [x] **Step 7:** full suite `uv run pytest -q` (2 files green), **commit** `feat(backend): uv scaffold, settings, CUDA bootstrap, compose postgres`.

### Task 2: Canonical schemas (transcript + job) — TDD

**Files:** Create `backend/src/stt_platform/schemas/{__init__,transcript,jobs,providers,errors}.py`; `backend/src/stt_platform/domain/{__init__,errors,status,timeline}.py`; Test `backend/tests/unit/test_schemas_transcript.py`, `test_schemas_jobs.py`, `test_timeline.py`.

**Interfaces (produces):**
- `TranscriptWord(start: float>=0, end: float>start, text: str, confidence: float|None 0..1)`
- `TranscriptSegment(start, end, text, confidence|None, words: list[TranscriptWord]=[])`
- `TranscriptResult(job_id, schema_version="1.0", text, language: str|None, segments, timestamp_granularity: Literal["segment","word"], audio_duration_s, processing_duration_s, provider, model, model_version: str|None, warnings: list[str]=[])`
- `JobStatus(str, Enum)` = ACCEPTED, VALIDATING, STORED, QUEUED, PROCESSING, SUCCEEDED, RETRY_WAIT, FAILED, CANCELLED; `ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]]` (PROCESSING→{SUCCEEDED,FAILED,RETRY_WAIT,CANCELLED}; FAILED→{QUEUED} for retry; QUEUED→{PROCESSING,CANCELLED}).
- `domain.errors`: `AudioValidationError(code, message)`, `UploadTooLarge`, `AudioTooLong`, `ProviderError(code, message, retryable)`, `ProviderNotFound`, `JobNotFound`, `JobNotReady`, `IdempotencyConflict`, `InvalidTransition`.
- `domain.timeline.shift_segments(segments, offset_s)` — returns shifted copies (segments + words), originals untouched.

- [x] **Step 1: failing tests** (key cases):

```python
def test_granularity_segment_forbids_word_timings():
    seg = TranscriptSegment(start=0.0, end=1.0, text="hi",
                            words=[TranscriptWord(start=0.0, end=0.5, text="hi")])
    with pytest.raises(ValidationError):
        make_result(granularity="segment", segments=[seg])

def test_segment_end_must_exceed_start():
    with pytest.raises(ValidationError):
        TranscriptSegment(start=1.0, end=0.5, text="x")

def test_segments_must_be_ordered_and_within_duration():
    # start=2.0 then start=1.0 -> ValidationError; end=10.6 vs duration 10.0 (tol 0.5) -> ValidationError

def test_granularity_word_requires_word_timings():
    # granularity="word" with no words -> ValidationError

def test_json_roundtrip():
    assert TranscriptResult.model_validate_json(result.model_dump_json()) == result

def test_transition_map_rejects_invalid():
    assert is_transition_allowed(JobStatus.SUCCEEDED, JobStatus.QUEUED) is False

def test_shift_segments_adds_offset_to_segments_and_words():
    shifted = shift_segments([seg], 5.0)
    assert shifted[0].start == seg.start + 5.0 and shifted[0].words[0].start == seg.words[0].start + 5.0
    assert seg.start == 0.0  # original unchanged
```

- [x] **Step 2:** `uv run pytest tests/unit/test_schemas_transcript.py tests/unit/test_schemas_jobs.py tests/unit/test_timeline.py -v` → FAIL (ImportError).
- [x] **Step 3:** implement — validators: `end > start`; ordered non-decreasing starts; `end <= audio_duration_s + 0.5`; granularity↔words rule; `text` stripped non-empty (segments; full text may be empty for silence); ErrorCode enum: `INVALID_REQUEST, UNSUPPORTED_MEDIA, PAYLOAD_TOO_LARGE, AUDIO_TOO_LONG, PROVIDER_NOT_FOUND, JOB_NOT_FOUND, JOB_NOT_READY, IDEMPOTENCY_CONFLICT, PROVIDER_ERROR, INTERNAL_ERROR, NOT_IMPLEMENTED`; `ErrorResponse(code, message, details|None)`; `JobFailure(code, message, retryable, provider_code|None)`; `AudioMetadata(format, codec, sample_rate, channels, duration_s)`; `JobRecord` as in architecture §7 with `datetime` (tz-aware) fields.
- [x] **Step 4:** all three files PASS; full suite PASS; **commit** `feat(backend): canonical transcript/job schemas, transition map, timeline shift`.

### Task 3: Audio inspection and validation — TDD

**Files:** Create `backend/src/stt_platform/audio/{__init__,inspect,validation}.py`; `backend/tests/audio_fixtures.py`; Test `test_audio_inspect.py`, `test_audio_validation.py`.

**Interfaces:**
- `inspect_audio(path: Path) -> AudioInfo(format_name: str, codec: str|None, sample_rate: int, channels: int, duration_s: float, size_bytes: int)` — ffprobe JSON, 15 s timeout, `CREATE_NO_WINDOW` on nt; raises `AudioValidationError("UNDECODABLE", …)` on failure/empty/zero-duration/no-audio-stream.
- `validate_upload(path: Path, settings: Settings) -> AudioInfo` — size check first (no probe) → `UploadTooLarge`; probe → duration > `max_sync_duration_s` → `AudioTooLong`; extension never consulted.
- Fixtures: `make_wav(tmp_path, seconds=2.0, freq=440, sr=16000)` via `ffmpeg -f lavfi -i "sine=frequency=440:duration=2" -ar 16000 -ac 1 out.wav`; `make_mp3` likewise.

- [x] **Step 1: failing tests**:

```python
def test_reads_content_regardless_of_extension(tmp_path):
    wav = make_wav(tmp_path); misnamed = tmp_path / "audio.bin"
    wav.rename(misnamed)
    info = inspect_audio(misnamed)
    assert info.format_name == "wav" and info.sample_rate == 16000 and info.channels == 1
    assert 1.8 < info.duration_s < 2.2

def test_rejects_text_bytes_with_wav_extension(tmp_path):
    p = tmp_path / "bomb.wav"; p.write_bytes(b"definitely not audio" * 10)
    with pytest.raises(AudioValidationError) as e: inspect_audio(p)
    assert e.value.code == "UNDECODABLE"

def test_rejects_empty_file(tmp_path):
    p = tmp_path / "empty.wav"; p.write_bytes(b"")
    with pytest.raises(AudioValidationError): inspect_audio(p)

def test_size_limit_checked_before_probe(tmp_path, settings, monkeypatch):
    monkeypatch.setattr("stt_platform.audio.validation.inspect_audio",
                        lambda *_: pytest.fail("probe must not run"))
    big = tmp_path / "big.bin"; big.write_bytes(b"\0" * (settings.max_upload_bytes + 1))
    with pytest.raises(UploadTooLarge): validate_upload(big, settings)

def test_duration_limit(tmp_path):
    wav = make_wav(tmp_path, seconds=2.0)
    with pytest.raises(AudioTooLong): validate_upload(wav, settings)
        # settings fixture: max_sync_duration_s=1.0
```

- [x] **Step 2:** run → FAIL; **Step 3:** implement (ffprobe wrapper as in architecture; `creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0`); **Step 4:** PASS + full suite; **commit** `feat(backend): audio inspection and content-based validation`.

### Task 4: Provider contract and registry — TDD

**Files:** Create `backend/src/stt_platform/providers/{__init__,base,registry}.py`; `backend/tests/fakes.py` (FakeProvider); Test `test_provider_contract.py`, `test_registry.py`.

**Interfaces:**
- `TranscriptionRequest(audio_path: Path, language: str|None, options: dict = {})`
- `ProviderTranscript(text, language, language_probability: float|None, segments: list[TranscriptSegment], words_available: bool, model: str, model_version: str|None, audio_duration_s: float, processing_duration_s: float)`
- `ProviderProtocol` (runtime-checkable Protocol): `id: str`, `name: str`, `is_local: bool`, `capabilities() -> ProviderCapabilities`, `health() -> ProviderHealth`, `transcribe(request) -> ProviderTranscript`.
- `ProviderCapabilities(languages: list[str], language_detection: bool, word_timestamps: bool, segment_timestamps: bool, diarization: bool, streaming: bool, batch: bool, async_processing: bool, confidence_scores: bool, max_file_bytes: int|None, max_duration_s: float|None, formats: list[str], capability_sources: dict[str, Literal["documented","verified"]] = {})`
- `ProviderHealth(status: Literal["ok","degraded","unavailable"], detail: str|None)`, `ProviderInfo(id, name, is_local, capabilities, health)`
- `ProviderRegistry.register(p)`, `.get(id)` (→ `ProviderNotFound`), `.all() -> list[ProviderInfo]`, `.default_id`.

- [x] **Step 1: failing tests** — contract suite applied to `FakeProvider`:

```python
def test_fake_provider_satisfies_contract():
    p = FakeProvider()
    assert isinstance(p, ProviderProtocol)
    caps = p.capabilities(); assert caps.segment_timestamps and caps.formats
    assert p.health().status in {"ok", "degraded", "unavailable"}
    tr = p.transcribe(TranscriptionRequest(audio_path=Path("x.wav"), language="en"))
    TranscriptResult(job_id="j", text=tr.text, language=tr.language, segments=tr.segments,
                     timestamp_granularity="word" if tr.words_available else "segment",
                     audio_duration_s=tr.audio_duration_s, processing_duration_s=tr.processing_duration_s,
                     provider=p.id, model=tr.model)  # must validate

def test_transient_error_is_marked_retryable():
    with pytest.raises(ProviderError) as e: FakeProvider(fail="transient").transcribe(req)
    assert e.value.retryable is True
    with pytest.raises(ProviderError) as e: FakeProvider(fail="permanent").transcribe(req)
    assert e.value.retryable is False

def test_registry_unknown_provider_raises(): # get("nope") -> ProviderNotFound
def test_registry_rejects_duplicate_ids():   # ValueError
```

- [x] **Step 2:** FAIL; **Step 3:** implement base + registry + `tests/fakes.py`; **Step 4:** PASS + suite; **commit** `feat(backend): provider contract, capabilities model, registry`.

### Task 5: Faster-Whisper adapter — TDD (unit + GPU)

**Files:** Create `backend/src/stt_platform/providers/faster_whisper.py`; Test `tests/unit/test_faster_whisper_unit.py`, `tests/gpu/test_faster_whisper_gpu.py`.

**Interfaces:** `FasterWhisperProvider(settings, engine_factory=None)`, `id="faster-whisper"`, `is_local=True`; lazy singleton engine (constructed on first `transcribe`, once per process); `WHISPER_LANGUAGE_CODES: list[str]` module constant (99 codes, documented source; test asserts len ≥ 95 and contains `en`, `hi`). Mapping helper `_to_provider_transcript(segments_iter, info, offset_s)` rounds to 3 decimals, strips text, maps `word.probability → confidence`.

- [x] **Step 1: failing unit tests** (fake engine object returning canned segments/words/info):

```python
def test_maps_segments_and_words_to_canonical(fake_settings):
    adapter = FasterWhisperProvider(fake_settings, engine_factory=lambda **_: FakeEngine())
    tr = adapter.transcribe(TranscriptionRequest(audio_path=Path("a.wav"), language="en"))
    assert tr.segments[0].text == "hello world"
    assert tr.segments[0].words[0].confidence == pytest.approx(0.9, abs=0.01)
    assert tr.words_available is True

def test_engine_constructed_once(fake_settings):  # two transcribe calls -> factory called once
def test_capabilities_documented_and_local(fake_settings):
    caps = adapter.capabilities(); assert caps.word_timestamps and not caps.async_processing
    assert caps.capability_sources.get("word_timestamps") in (None, "documented", "verified")

def test_decode_failure_is_permanent(fake_settings):
    # engine.transcribe raises ValueError("Failed to decode") -> ProviderError(retryable=False)
def test_inference_crash_is_transient(fake_settings):
    # engine.transcribe raises RuntimeError -> ProviderError(retryable=True)
```

- [x] **Step 2:** FAIL; **Step 3:** implement adapter (bootstrap via `core.cuda` import first; `engine = WhisperModel(settings.whisper_model, device=settings.device, compute_type=settings.compute_type, download_root=str(settings.model_dir), cpu_threads=4)`; `apply_runtime_env()` before construction; transcribe params `beam_size=5, vad_filter=True, word_timestamps=True, condition_on_previous_text=False`).
- [x] **Step 4:** unit PASS; **Step 5: GPU tests** (`-m gpu`, skip if `not torch-free cuda?` → skip if `ctranslate2.get_cuda_device_count() == 0` or `audio/sample.wav` missing):

```python
def test_gpu_transcribes_sample_with_word_timestamps(...):
    tr = adapter.transcribe(req)  # real large-v3-turbo, cached
    assert tr.language == "en" and tr.segments and tr.words_available
    starts = [s.start for s in tr.segments]; assert starts == sorted(starts)
    assert all(0 <= s.start < s.end <= tr.audio_duration_s + 0.5 for s in tr.segments)
    # writes evidence to results/phase1_gpu_smoke.json (rtf, timings)
```

Run: `uv run pytest -m gpu -q` (expect ~10-20 s incl. 4.7 s load). **Step 6:** full suite + **commit** `feat(backend): faster-whisper adapter with cached CUDA model`.

### Task 6: PostgreSQL persistence + migrations — TDD

**Files:** Create `backend/src/stt_platform/persistence/{__init__,db,models,repositories}.py`; `backend/migrations/` (alembic init, `env.py` wired to `get_settings().database_url` + `Base.metadata`; autogen revision reviewed); Test `tests/integration/conftest.py`, `test_persistence.py`, `test_migrations.py`.

**Interfaces:** `JobRepository(session)`: `create(job: JobRecord) -> JobRecord`; `get(job_id, scope="local") -> JobRecord` (raise `JobNotFound`); `find_by_idempotency(scope, key) -> JobRecord|None`; `append_transition(job_id, from_status, to_status, detail=None)` (validates via `ALLOWED_TRANSITIONS` → `InvalidTransition`); `set_status(job_id, to_status, **fields)`; `save_result(job_id, transcript: TranscriptResult)`; `load_result(job_id) -> TranscriptResult|None`. Tables: `jobs` (uuid str pk, scope, status varchar(16), requested_provider, actual_provider, model, model_version, language_requested, language_detected, audio_format/codec/sample_rate/channels/duration_s, audio_path, idempotency_key, request_fingerprint, attempt_count, failure jsonb, options jsonb, transcript jsonb, created_at/updated_at/started_at/completed_at, unique index on (scope, idempotency_key) where key not null), `job_transitions` (id serial, job_id fk, from_status, to_status, detail jsonb, at).

- [x] **Step 1: conftest** (exact):

```python
import os
os.environ.setdefault("TESTCONTAINERS_HOST_OVERRIDE", "127.0.0.1")
@pytest.fixture(scope="session")
def pg_url():
    from testcontainers.postgres import PostgresContainer
    with PostgresContainer("postgres:16") as pg:
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        yield url
```

- [x] **Step 2: failing tests** — create/read round-trip; transition trail append + invalid transition raises; unique idempotency lookup; result save/load round-trip; migration test: `alembic upgrade head` → tables exist (`inspect(engine).get_table_names()`), `downgrade base` → gone.
- [x] **Step 3:** run `uv run pytest tests/integration/test_persistence.py -v` → FAIL (no module / no tables). **Step 4:** implement models + alembic; **Step 5:** PASS + suite; **commit** `feat(backend): postgres persistence and alembic migrations`.

### Task 7: Job service — idempotent creation, sync processing — TDD

**Files:** Create `backend/src/stt_platform/services/{__init__,jobs}.py`; Test `tests/integration/test_job_service.py`.

**Interfaces:** `JobService(repo, registry, settings)`:
- `create_and_process(*, audio_path: Path, provider_id: str|None, language: str|None, idempotency_key: str|None, options: dict|None) -> JobRecord`
- `retry(job_id, scope="local") -> JobRecord` (FAILED+retryable only, else `InvalidTransition`/`JobNotReady`)
- `get(job_id, scope)`, `get_result(job_id, scope)` (→ `TranscriptResult`; `JobNotReady` unless SUCCEEDED; `JobNotFound` cross-scope)
- fingerprint = `sha256(json(params) + sha256(file bytes))`, hex.

**Behavior:** idempotent replay returns original row without reprocessing; different fingerprint same key → `IdempotencyConflict`. Happy path transition trail exactly `ACCEPTED→VALIDATING→STORED→QUEUED→PROCESSING→SUCCEEDED` (each persisted). Validation failure → `FAILED` (failure.retryable False, code from `AudioValidationError`); transient provider failure → `FAILED` retryable True; `retry()` → second attempt succeeds → trail shows second `QUEUED→PROCESSING→SUCCEEDED` block, `attempt_count==2`. `provider_id=None` → registry default; unknown → `ProviderNotFound`.

- [x] **Step 1: failing tests** (integration: real PG container + `FakeProvider` + generated wav fixture; assert trail order from `job_transitions`; assert duplicate submission → same `job.id` and exactly one `PROCESSING` transition). **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS + suite; **commit** `feat(backend): job service with idempotency and state trail`.

### Task 8: API `/v1` — TDD

**Files:** Create `backend/src/stt_platform/main.py`, `api/{__init__,deps,v1/{__init__,transcriptions,providers,health,errors}}.py`; Test `tests/integration/test_api_transcriptions.py`, `test_api_providers_health.py`, `test_openapi.py`.

**Interfaces:** `create_app(settings=None, registry=None) -> FastAPI`; module-level `app` for uvicorn. `POST /v1/transcriptions` (multipart `file`, form `provider`, `language`; header `Idempotency-Key`) → **201** `JobRecord` JSON + `Location` header; invalid media → structured error; `GET /v1/transcriptions/{id}` → 200/404; `GET /v1/transcriptions/{id}/result` → 200/409/404; `POST …/retry` → 200/409; `POST …/cancel` → **501** `NOT_IMPLEMENTED` (Phase 2); `GET /v1/providers`; `GET /v1/health/live` (always 200), `GET /v1/health/ready` (DB `SELECT 1`; 503 when down; provider health NOT gating).

**Error mapping (pinned):** UNDECODABLE→415 `UNSUPPORTED_MEDIA`; UploadTooLarge→413; AudioTooLong→422; ProviderNotFound→422; JobNotFound→404; JobNotReady→409; IdempotencyConflict→409; ProviderError→502; unknown→500 `INTERNAL_ERROR`; all bodies `ErrorResponse`.

- [x] **Step 1: failing tests** — full fake-provider flow over ASGI (`httpx.ASGITransport`): upload generated wav → 201 → `GET` shows SUCCEEDED → result equals fake transcript; idempotency replay → same id, single job; text-as-wav → 415 body `UNSUPPORTED_MEDIA`; unknown provider → 422; cancel → 501; ready endpoint with DB down (bad url override) → 503; `test_openapi.py` asserts the 8 paths exist and POST request body contains the multipart schema.
- [x] **Step 2:** FAIL. **Step 3:** implement routers + handlers + deps (service built in app state; tests inject fake registry + pg container via dependency override). **Step 4:** PASS + suite; **commit** `feat(backend): v1 transcription, providers, health endpoints`.

### Task 9: Wire-up, docs, phase report

- [x] **Step 1:** `main.py` app factory final; `backend/.env.example` (`STT_DATABASE_URL=…25432…`); `backend/README.md` (uv sync / compose / run / test commands incl. `-m gpu`); `docs/api.md` (endpoint walkthrough + example curl/JSON for the real flow); `docs/reliability.md` (Phase 1 semantics: transition trail, idempotency, what awaits Phase 2); root `README.md` status → Phase 1 complete.
- [x] **Step 2:** end-to-end manual run against real stack: compose PG up; `uv run uvicorn stt_platform.main:app --port 8200`; `curl -F file=@audio/sample.wav http://127.0.0.1:8200/v1/transcriptions` → real transcription; fetch result; verify `/v1/providers`, health endpoints. Record actual outputs.
- [x] **Step 3:** full gates: `uv run pytest -q` (unit+integration) **and** `uv run pytest -m gpu -q` — record real counts in report.
- [x] **Step 4:** write `docs/phase-1-report.md` (implemented, decisions, files, deps, commands, actual test results, limitations, next phase) and **commit** `docs(backend): phase 1 report and run documentation`.

## Self-review

- **Spec coverage:** §7 schemas→T2; §8 audio→T3; §6 contract→T4/T5; §11 PG→T6; §10 idempotency basics→T7; §12 API→T8; §17 unit/integration tests→each task; Phase 1 deliverables (backend package, FastAPI+Pydantic+PG, canonical schema, FW adapter, audio validation, sync short-audio, versioned API, tests)→T1–T9. Deferred by design: Temporal/outbox/cancel (Phase 2), word-level beyond local provider (Phase 4), OTel (Phase 5), MLflow (Phase 6).
- **Placeholder scan:** every step has concrete code, commands, and expected outcomes; no TBDs.
- **Type consistency:** `create_and_process`, `TranscriptResult.timestamp_granularity`, `AudioValidationError.code`, `shift_segments`, `ProviderTranscript.words_available`, `JobRepository` methods are referenced consistently across tasks.
- **Review Focus pinning:** (1)→T3 `test_rejects_*`, T8 415 case; (2)→T2 ordering/bounds validators, T5 GPU bounds; (3)→T7 duplicate-replay test; (4)→T4 registry + T8 unknown-provider; (5)→T3 size-before-probe + T8 413.
