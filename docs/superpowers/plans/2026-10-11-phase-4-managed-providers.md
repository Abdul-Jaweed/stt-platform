# Phase 4 — Managed Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Sarvam, Deepgram, and ElevenLabs Scribe behind the same internal provider contract, with async-provider support in the workflow (submit → poll → fetch), per-class concurrency limits, and honest capability reporting (`documented` vs `verified`).

**Architecture:** Managed adapters share an HTTP base (httpx) that centralizes auth, timeouts, error normalization (transient vs permanent), and a per-adapter concurrency semaphore. Sync managed providers (Deepgram pre-recorded, ElevenLabs Scribe) plug into the existing `transcribe()` path. Sarvam's batch API is genuinely asynchronous → the workflow gains a `pending` outcome plus `poll_remote`/`cancel_remote` activities; the remote job reference is persisted in a new `provider_attempts` table so a worker crash can never resubmit (no duplicate provider charges). Async-capable providers always take the async path, even for short audio. Providers register only when their API key is configured; capabilities start as `documented` and only become `verified` via live tests with a real key. Amazon Transcribe stays deferred (AWS setup unverified — spec Phase 4 says "after requirements and AWS setup are verified").

**Tech Stack:** httpx (runtime), respx (dev, httpx mocking), existing Temporal/PG/S3 stack. All adapter tests are mocked — **no paid API calls in gates** (spec DoD).

**Spec:** `I:\Transcription-Pipeline\IDEA.md` §Initial providers, §Provider contract, §Durable jobs (concurrency split, reconciliation), §Storage (provider attempts).

## Global Constraints

- **No paid calls in ordinary tests.** Live verification is a marked, key-gated test (`-m live_provider`, skipped without `STT_<PROVIDER>_API_KEY`) — never part of the default gates.
- **Never fabricate capabilities or timestamps.** `capability_sources` marks each capability `documented` (vendor docs) or `verified` (our live test); adapters drop degenerate timestamps rather than clamp; missing confidence/speaker stays `None`.
- **Never silently route to cloud**: provider choice stays explicit; managed adapters receive only what the caller explicitly submitted to them.
- Keys only from settings/env (`STT_SARVAM_API_KEY`, `STT_DEEPGRAM_API_KEY`, `STT_ELEVENLABS_API_KEY`); never logged, never in git, never in the frontend.
- Vendor API shapes must be verified against vendor docs (web) at implementation time; source links go in the adapter docstring.
- All timestamps refer to the original audio timeline; adapters probe duration locally (ffprobe) and validate canonical constraints before returning.
- Async providers: a crash between submit and poll must never re-submit; retry of a FAILED job is a *new* attempt row (documented cost caveat).

## Review Focus

1. **Worker crash during polling** → workflow replay resumes polling from the persisted ref; `provider_attempts` shows exactly one submission.
2. **Degenerate vendor timestamps** (end ≤ start, out-of-order) → dropped/ordered with a `warnings` entry, never clamped silently.
3. **429 / 5xx / timeout** → transient `ProviderError` → RETRY_WAIT trail; **400/401/403** → permanent FAILED, no retry storm.
4. **Concurrency** — GPU and managed calls have independent caps: a managed call must not starve the single GPU slot.
5. **Short audio + async provider** → forced onto the 202/workflow path (no inline sync attempt).

---

### Task 1: Async provider contract + workflow polling + provider_attempts

**Files:**
- Create: `backend/migrations/versions/<rev>_provider_attempts.py`, `backend/src/stt_platform/persistence/provider_attempts.py` (repository), `backend/tests/integration/test_workflow_remote.py`, `backend/tests/unit/test_async_provider_contract.py`
- Modify: `providers/base.py` (async protocol), `persistence/models.py` (`ProviderAttemptRow`), `workers/activities.py` (`execute_attempt` pending outcome, `poll_remote`, `cancel_remote`, shared `_finalize`), `workflows/transcription.py` (poll loop), `core/config.py` (`remote_poll_interval_s=5.0`, `remote_max_wait_s=7200.0`, `managed_concurrency=4`), `workers/transcription_worker.py` (pass remote config, split concurrency), `schemas/transcript.py` (`TranscriptWord.speaker: int | None = None`, `TranscriptSegment.speaker: int | None = None`), `services/jobs.py` (async-capable provider ⇒ force async path)

**Interfaces (produces):**
- `AsyncProviderProtocol` (additive): `submit(request) -> RemoteJobRef`; `poll(ref) -> RemoteJobState`; `cancel(ref) -> bool` (False = unsupported); `RemoteJobRef {provider_job_id, raw?}`; `RemoteJobState {state: Literal["pending","completed","failed"], transcript: ProviderTranscript | None, error_code/message/retryable, raw}`.
- `execute_attempt` outcome dict gains `{"outcome": "pending", "provider_job_id": str}`.
- Workflow payload gains `remote: {poll_interval_s, max_wait_s}`.
- `ProviderAttemptRepository(session)`: `record(job_id, scope, attempt, provider_id, provider_job_id, state)` (upsert per attempt), `mark(job_id, attempt, state, detail=None)`, `list_for_job(job_id, scope)`.

**Tests:**
```python
# test_async_provider_contract.py (unit, fake async provider)
test("submit/poll protocol is satisfied by FakeAsyncProvider")
test("sync providers are not async-capable")
# test_workflow_remote.py (live Temporal, in-process worker, fake async provider)
test("async job: submit once, poll to completion, SUCCEEDED")     # trail: QUEUED→PROCESSING→SUCCEEDED; exactly 1 submit call
test("async job: poll failure retryable → RETRY_WAIT → new attempt submits again")
test("async job: cancellation during polling lands CANCELLED")
test("async job: max wait exceeded → transient timeout failure")
test("worker crash mid-poll resumes without resubmitting")        # kill worker, restart, assert submit count == 1
```

- [x] Steps: write failing tests → RED (live Temporal up) → implement → GREEN → `uv run pytest -q` full suite → commit `feat(backend): async provider contract, workflow polling, provider_attempts (T1)`.

### Task 2: Managed HTTP base + registry wiring + concurrency split

**Files:**
- Create: `providers/managed/__init__.py`, `providers/managed/http_base.py`, `tests/unit/test_http_base.py`, `tests/integration/test_registry_wiring.py`, `tests/conftest.py` add `respx_mock` fixture
- Modify: `core/config.py` (keys + base URLs + `managed_concurrency`), `main.py` + `workers/transcription_worker.py` (`build_default_registry(settings)` registers configured managed providers), `workers/transcription_worker.py` (`max_concurrent_activities = gpu_concurrency + managed_concurrency + 2`; GPU semaphore inside the whisper adapter), `providers/faster_whisper.py` (semaphore), `backend/pyproject.toml` (`uv add httpx`, `uv add --dev respx`)

**Interfaces (produces):**
- `ManagedHttpClient`: `__init__(base_url, api_key, auth_header, timeout_s, max_response_bytes, base_url_override=None)`; `request(method, path, **kw) -> dict` with: auth injection, `ProviderError` mapping (408/429/5xx → retryable; 400/401/403/404/413/422 → permanent; network error → retryable `NETWORK_ERROR`), response-size guard, redacted logging.
- `managed_semaphore = threading.BoundedSemaphore(settings.managed_concurrency)` passed into adapters.
- `build_default_registry(settings)`: registers `faster-whisper` always; `deepgram`/`elevenlabs`/`sarvam` only when their key is set (base URL override honored for tests/fake servers).

**Tests:**
```python
test("auth header is attached and key never appears in errors")   # respx asserts header; error text has no key
test("429 maps to retryable RATE_LIMITED, 401 to permanent AUTH_ERROR")
test("network failure maps to retryable NETWORK_ERROR")
test("oversized response is rejected")
test("registry registers managed providers only when keys are set")
test("managed semaphore caps in-flight calls (threads, cap=2)")
test("gpu semaphore serializes whisper calls while managed calls proceed")  # fake slow providers, overlap asserted
```

- [x] Steps as usual (RED → GREEN) → commit `feat(backend): managed http base, key-gated registry, concurrency split (T2)`.

### Task 3: Deepgram adapter (sync, word-level)

**Files:** `providers/managed/deepgram.py`, `tests/unit/test_deepgram_adapter.py`, `tests/fixtures/deepgram_listen.json` (synthesized vendor payload)

**Interfaces:** `DeepgramProvider` (id `deepgram`, is_local False): POST `{base}/v1/listen?model=nova-3&smart_format=true&punctuate=true&language=<opt>`; body = audio bytes, `Content-Type` from the file; auth `Authorization: Token <key>`. Response → `ProviderTranscript`: `results.channels[0].alternatives[0]` → `transcript`, `words[]` (word/punctuated_word, start, end, confidence, speaker when `diarize=true`), segment = utterance grouping when present else chunk by sentence? — no: build segments from words with a deterministic grouping (punctuation boundaries), each segment's words attached; `language` from `results.channels[0].detected_language`; `model` `nova-3`; `raw` = trimmed response. Capabilities: word_timestamps True (documented), diarization True (documented), async_processing False, confidence True, `capability_sources` all `documented`.

**Tests:** request shape (query params, auth header, body bytes); normalization (words with punctuated_word preferred, speaker ints, confidence); degenerate words dropped with warning; 429 → retryable; malformed JSON → permanent.

- [x] Steps as usual → commit `feat(backend): deepgram adapter (T3)`.

### Task 4: ElevenLabs Scribe adapter (sync, word-level)

**Files:** `providers/managed/elevenlabs.py`, `tests/unit/test_elevenlabs_adapter.py`, `tests/fixtures/elevenlabs_stt.json`

**Interfaces:** `ElevenLabsProvider` (id `elevenlabs`): POST `{base}/v1/speech-to-text` multipart (`file`, `model_id=scribe_v1`, optional `language_code`); auth `xi-api-key`. Response `{text, language_code, words: [{text, start, end, type, speaker_id?}]}` — keep only `type == "word"` entries; `speaker_id` → int mapping (`"speaker_0"` → 0) when present. Capabilities: word_timestamps True (documented), diarization True (documented), confidence False (Scribe does not return per-word confidence — do not fabricate), async_processing False.

**Tests:** multipart shape; spacing entries filtered; speaker mapping; no confidence fabricated (None); error mapping; malformed → permanent.

- [x] Steps as usual → commit `feat(backend): elevenlabs scribe adapter (T4)`.

### Task 5: Sarvam adapter (async batch) + workflow integration

**Files:** `providers/managed/sarvam.py`, `tests/unit/test_sarvam_adapter.py`, `tests/fixtures/sarvam_*.json`, `tests/integration/test_workflow_sarvam.py` (live Temporal + respx)

**Interfaces:** `SarvamProvider` (id `sarvam`): async_processing True; `submit`: POST `{base}/speech-to-text/job` multipart (file + `job_parameters` JSON: model `saarika:v2.5`, `language_code` when given, `with_diarization`/timestamps flags) → `{job_id}`; `poll`: GET `/speech-to-text/job/{id}/status` → `job_state` (map `Completed`→completed, `Failed`→failed, else pending); on completed: GET `/speech-to-text/job/{id}/download-files` → file URL(s) → download JSON → entries `{transcript, start_time_seconds, end_time_seconds, speaker_id?}` → segments (+words only if the payload actually carries word timings — otherwise segment granularity, honestly); `cancel` → returns False (no documented cancel — documented in the report). Auth header `api-subscription-key`. Exact field names verified against vendor docs at implementation time (links in docstring).

**Tests:**
```python
# unit (respx): submit request shape; status mapping; result download + normalization;
# failed job maps error; diarization speakers; segment-only granularity when no words
# integration (live Temporal): job requesting sarvam runs submit → 2 polls → SUCCEEDED;
# provider_attempts has exactly 1 SUBMITTED row; result provider == "sarvam"
```

- [x] Steps as usual → commit `feat(backend): sarvam async adapter + workflow integration (T5)`.

### Task 6: Live-test scaffolding + fake-server e2e + docs hooks

**Files:** `tests/integration/test_live_providers.py` (marked `live_provider`, `pytest.mark.skipif` no key; transcribes `audio/sample.wav` and asserts word timestamps + flips `capability_sources` to `verified` for the run only), `pyproject.toml` (`markers`), `scripts/e2e_phase4.py` (fake Sarvam server on :8765 emulating submit/status/download; starts API+worker with `STT_SARVAM_API_KEY=test` + `STT_SARVAM_BASE_URL=http://127.0.0.1:8765`; creates a job requesting sarvam; polls to SUCCEEDED; asserts transcript + attempt row; writes `results/phase4_e2e/result.json`)

**Tests:** the e2e script is the evidence; a smoke unit test asserts the live tests are deselected without keys (`pytest -m live_provider --collect-only` empty).

- [x] Steps: write script → run against compose stack (foreground with generous timeout) → green → commit `test(backend): live-provider scaffolding + fake-server e2e (T6)`.

### Task 7: Documentation + report

**Files:** `docs/providers.md` (capability matrix: per provider — endpoint, auth, timestamps, diarization, confidence, limits, documented-vs-verified, cost notes), `docs/phase-4-report.md`, `README.md` (status + provider list + env vars), `backend/.env.example` (three keys, empty), plan ticked.

- [x] Steps: write docs → final gates (`pytest -q`, `-m gpu`, frontend `npm test` untouched but re-run for the record) → commit `docs(phase4): provider matrix and phase report (T7)`.

## Self-review

- **Spec coverage:** adapters for Sarvam/Deepgram/ElevenLabs ✓ (T3–T5); Amazon Transcribe deferred with stated reason ✓; capability discovery + documented/verified ✓ (schemas already carry `capability_sources`); async provider jobs + polling + cancel-where-supported ✓ (T1/T5); separate CPU/GPU/managed concurrency ✓ (T2); reconciliation of submissions (no duplicate submit across crashes) ✓ (T1); "no paid calls in CI" ✓ (constraint + T6); never fabricate ✓ (drop/None policy); privacy (explicit routing) ✓ (documented in T7).
- **Placeholder scan:** no TBDs; vendor field names are explicitly flagged for doc verification at implementation time (with fallback: adapter raises permanent MALFORMED_RESPONSE if the shape differs — never guesses).
- **Type consistency:** `RemoteJobRef`/`RemoteJobState` defined once in `providers/base.py` and consumed by workflow activities only; `ProviderAttemptRepository` methods single-sourced in T1.
- **Review Focus:** each item pinned by a named test — crash-resume (T1), degenerate timestamps (T3/T4/T5), 429/5xx vs 4xx (T2/T3), concurrency split (T2), async-forced path (T1).
