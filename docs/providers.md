# Providers

Every provider implements one internal contract
(`stt_platform.providers.base`): either synchronous
(`transcribe(request) -> ProviderTranscript`) or asynchronous
(`submit` / `poll` / `cancel` with `RemoteJobRef` / `RemoteJobState`).
Nothing outside `providers/` knows which vendor sits behind a provider id.
A managed provider registers **only when its API key is configured**
(`build_default_registry`, `providers/defaults.py`); the local engine is
always present and is the default.

## Capability matrix

| | `faster-whisper` | `deepgram` | `elevenlabs` | `sarvam` |
|---|---|---|---|---|
| Transport | in-process CUDA | `POST /v1/listen` | `POST /v1/speech-to-text` | batch job API |
| Auth | — | `Authorization: Token …` | `xi-api-key` | `api-subscription-key` |
| Execution | sync | sync | sync | **async** (submit → poll → fetch) |
| Default model | `large-v3-turbo` | `nova-3` | `scribe_v2` | `saaras:v4` |
| Word timestamps | yes — *verified* | yes — *verified* | yes — *verified* | **no** (segment only) |
| Segment timestamps | yes — *verified* | yes — *verified* | derived from word runs | yes — *verified* |
| Diarization | no | yes — *verified* | yes — *verified* (2026-10-12 run) | yes — *verified* |
| Word confidence | yes — *verified* | yes — *verified* | **no** — stays `None` | no |
| Language detection | yes | yes — *verified* | yes — *verified* | yes — *verified* |
| Cancel | — | — | — | **unsupported** (documented) |
| Model selection | `STT_WHISPER_MODEL` | fixed `nova-3` | `STT_ELEVENLABS_MODEL_ID` | fixed `saaras:v4`, `mode=transcribe` |

**Provenance rule.** `capability_sources` marks each capability
`documented` (vendor documentation) or `verified` (our own live test with a
real key). Deepgram, ElevenLabs, and Sarvam were live-verified on
2026-10-11 — one 10-second transcription per provider, free tier, STT
endpoints only; see `docs/live-provider-verification.md` for the numbers,
the five defects that run uncovered, and the evidence paths. The flips are
pinned by `tests/unit/test_capability_provenance.py`; a capability stays
`documented` until a live run exercises it (ElevenLabs diarization today).

## Declared limits (and where each number comes from)

Two different sets of caps apply to every upload, and the UI shows both:

1. **This deployment's** — `STT_MAX_UPLOAD_BYTES` (100 MB),
   `STT_MAX_SYNC_DURATION_S` (180 s, transcribed while you wait), and
   `STT_MAX_ASYNC_DURATION_S` (14400 s = 4 h, the ceiling for any job).
   Served to clients on `GET /v1/providers` as `limits`.
2. **The chosen provider's** — the vendor's own documented numbers, carried
   in `ProviderCapabilities.max_file_bytes` / `max_duration_s`. Both are
   enforced *before* a vendor call (`audio/limits.py::check_provider_limits`,
   wired into the API pre-flight, `JobService.create_async`, the synchronous
   `_process`, and the worker's `transcribe` activity), so an over-limit file
   is refused with the vendor's name in the message instead of failing in
   the cloud. `None` means the vendor documents no such limit — the platform
   then refuses nothing on its behalf.

| Provider | Max file | Max duration | Source (checked 2026-10-12) |
|---|---|---|---|
| `faster-whisper` | none | none | local engine — only the deployment's caps apply |
| `deepgram` | 2 GB | none | <https://developers.deepgram.com/docs/pre-recorded-audio> — "File size: Maximum 2 GB"; no duration limit is documented for pre-recorded audio |
| `elevenlabs` | 3 GB | 10 h | <https://elevenlabs.io/docs/overview/capabilities/speech-to-text> — "Files up to 3 GB", "Maximum duration: 10 hours (standard mode)". The endpoint reference also states a 5 GB ceiling for `file` uploads; the conservative overview figure is what the API advertises |
| `sarvam` | none | 2 h | <https://docs.sarvam.ai/api-reference-docs/speech-to-text/apis/overview> — "Batch API: max file duration 2 hours per file". The 30 s cap belongs to the synchronous REST endpoint this adapter does not use; no batch file-size limit is documented |

Every populated value carries `capability_sources[...] = "documented"` and is
pinned by `backend/tests/unit/test_capability_provenance.py`; the enforcement
itself is pinned by `test_provider_limits.py`, the API pre-flight test in
`test_api_transcriptions.py`, and a service-level test in
`test_job_service.py`.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `STT_DEEPGRAM_API_KEY` | *(unset)* | registers `deepgram` |
| `STT_DEEPGRAM_BASE_URL` | `https://api.deepgram.com` | override for fake servers |
| `STT_ELEVENLABS_API_KEY` | *(unset)* | registers `elevenlabs` |
| `STT_ELEVENLABS_BASE_URL` | `https://api.elevenlabs.io` | override |
| `STT_ELEVENLABS_MODEL_ID` | `scribe_v2` | `scribe_v1` still selectable |
| `STT_SARVAM_API_KEY` | *(unset)* | registers `sarvam` |
| `STT_SARVAM_BASE_URL` | `https://api.sarvam.ai` | override |
| `STT_PROVIDER_TIMEOUT_S` | `300` | per-request timeout |
| `STT_PROVIDER_CONNECT_TIMEOUT_S` | `10` | connect timeout |
| `STT_MAX_PROVIDER_RESPONSE_BYTES` | `25000000` | response-size guard |
| `STT_MANAGED_CONCURRENCY` | `4` | in-flight managed calls |
| `STT_GPU_CONCURRENCY` | `1` | in-flight GPU transcriptions |

## Routing and privacy

- **Provider choice is explicit** (`provider` form field). There is no
  silent fallback: a request that names a provider either runs there or
  fails. Locally-restricted audio is never re-routed to the cloud.
- **Async-capable providers always take the 202/workflow path**, even for
  short audio — a synchronous call to `sarvam` is impossible by
  construction (`provider_requires_async`).
- **Credential hygiene**: keys come only from settings/env; they are never
  logged, never embedded in errors (the HTTP base redacts them from vendor
  response bodies), and **never sent to presigned storage URLs** — Sarvam's
  upload/download transfers use a credential-free client.
- **Concurrency split**: GPU work is serialized by a semaphore inside the
  whisper adapter; managed HTTP calls share one semaphore
  (`managed_concurrency`). Worker activity slots cover both plus overhead,
  so a burst of managed calls cannot starve the single GPU slot.

## Error mapping (shared HTTP base)

`ManagedHttpClient` normalizes every failure into `ProviderError`:

| Condition | Code | Retryable |
|---|---|---|
| 408 / 425 / 5xx | `HTTP_<status>` | yes |
| 429 | `RATE_LIMITED` | yes |
| connect/read timeout | `TIMEOUT` | yes |
| transport error | `NETWORK_ERROR` | yes |
| 401 / 403 | `AUTH_ERROR` | no |
| other 4xx | `HTTP_<status>` | no |
| oversized response | `RESPONSE_TOO_LARGE` | no |
| invalid JSON / unexpected shape | `MALFORMED_RESPONSE` | no |
| unsupported requested language | `UNSUPPORTED_LANGUAGE` | no |

Retryable failures feed the workflow's bounded backoff + jitter
(`RETRY_WAIT` transitions); permanent failures end the job as `FAILED`
without a retry storm.

## Fidelity rules (all adapters)

- Timestamps always refer to the original audio timeline; degenerate
  spans (`end <= start`) are **dropped with a warning** — never clamped.
- Confidence is reported only when the vendor provides a per-word value
  (ElevenLabs Scribe does not: `confidence` stays `None`, and the
  `logprob` it does return is not converted into a fabricated score).
- Word timings are reported only when they exist (Sarvam batch: segment
  granularity, `words_available = False`).
- `audio_duration_s` comes from the vendor when it reports one
  (Deepgram), otherwise from a local ffprobe (ElevenLabs, Sarvam).
- Vendor language codes are kept verbatim (`eng`, `en-IN`); nothing is
  remapped to a friendlier value.

## Asynchronous providers (Sarvam batch)

`submit` persists a `provider_attempts` row **before** the provider call
(`SUBMITTING` → `SUBMITTED` + `provider_job_id`). A crash between submit
and poll can therefore never re-submit the same attempt (no duplicate
charges); a retry of a `FAILED` job is a new attempt row. The workflow
polls with durable sleeps (`remote_poll_interval_s`, bounded by
`remote_max_wait_s` → transient `REMOTE_TIMEOUT`). Cancellation calls the
provider's cancel where it exists and always records `CANCELLED`.

## Adding a provider

1. Implement the contract in `providers/managed/<name>.py` (reuse
   `ManagedHttpClient`; verify the vendor shapes against live docs and put
   the doc links in the module docstring).
2. Register it in `providers/defaults.py`, key-gated.
3. Unit tests with synthesized fixtures under respx (no paid calls) plus,
   where the provider is async, a live-Temporal integration test.
4. Add a `-m live_provider` test for the paid verification run.
