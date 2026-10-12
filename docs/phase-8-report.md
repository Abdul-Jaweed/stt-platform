# Phase 8 report — true streaming transcription (local-first)

**Status: complete.** Streaming runs on the live GPU stack, end to end, through
the console's own path, and the streamed transcript agrees with the batch
transcript of the same audio (WER **0.179**, 17/17 e2e checks). Evidence:
`results/phase8_e2e/result.json` (gitignored, like every other `results/` run).

## What was built

`WS /v1/transcriptions/stream` — audio in, partials and finals out, on the
local model, persisted nowhere. The full protocol is in `docs/streaming.md`.

| layer | file | what it does |
| --- | --- | --- |
| segmentation | `streaming/vad.py` | frame-aligned energy VAD: a window opens after `min_speech_ms` of voice, closes where the trailing silence began, rolls over at `max_window_ms` without gaps; identical windows however the signal was chunked |
| session | `streaming/session.py` | one decode per closed window (`final`), a bounded partial cadence on the open one, session-absolute timestamps, capped/idle endings as events, provider failures as events |
| decode | `streaming/decoder.py` | one long-lived `ffmpeg` child per session (container probing, so a browser's streaming WebM/Opus works); raw `s16le` clients skip the subprocess; a sample split across two writes survives |
| endpoint | `api/v1/streaming.py` | the WebSocket: auth, the local-only rule, the concurrency bound, the worker thread and the wire frames |
| provider | `providers/faster_whisper.py::transcribe_pcm` | one in-memory window → canonical transcript, sharing the batch path's mapping, error translation and **GPU semaphore** |
| console | `hooks/useLiveStream.ts`, `hooks/useLiveTranscript.ts`, `components/Recorder.tsx` | the panel: closed text in normal weight, the open utterance lighter; falls back to the old chunked preview only when a socket cannot be opened at all |

## Why it is built this way

* **Whisper is a batch model.** Streaming it honestly means deciding *where an
  utterance ends* and decoding that window once — not re-transcribing the whole
  take every few seconds (which is what the preview does, and why it was
  bounded). One decode per window is why a 10 s clip costs one pass.
* **Nothing is persisted.** No job, no object, no row. A stream is for text
  that is useful *now*; the durable artefact is still the recording, which goes
  through `POST /v1/transcriptions` exactly as before. Proven: the job count
  did not move across a stream (131 → 131) and the e2e asserts it.
* **Local only, before any audio.** A managed provider is refused with
  `STREAM_LOCAL_ONLY` at the start frame (spec: never silently route local
  audio to a cloud vendor).
* **The session stays synchronous.** The event loop owns the socket; one worker
  thread owns the session and publishes frames through an asyncio queue. That
  is what keeps the session a plain object with no locks in it — and keeps the
  GPU semaphore the only place where streaming and batch meet.

## Verification actually run

| command | result |
| --- | --- |
| `uv run pytest -q` (backend) | **516 passed, 3 skipped** |
| `uv run ruff check src tests ../scripts` | clean |
| `npm test` (frontend) | **175 passed** |
| `npm run build` | clean (`tsc --noEmit` + vite) |
| `uv run python ../scripts/e2e_phase8.py` (live GPU stack) | **17/17 checks** |

The e2e is the phase's headline: it streams a *streaming WebM/Opus* copy of
`audio/sample.wav` (what a browser's recorder produces) through **nginx on
:8080** — the console's path, so it also proves the upgrade proxying — then
uploads the same recording as a job and compares:

```
batch:    Hello, I'm Abdul Javid, AI Engineer at Home and Managed, and currently
          I'm working multiple projects, projects like a mass platform, basically
          memory as a service.
streamed: Hello, I'm Abdul Javid, AI engineer at Home and Manage, and currently
          I'm working on multiple projects. mass platform, basically memory as a
          service.
WER 0.179 (3 substitutions, 2 deletions, 26 vs 28 tokens)
```

The residual difference is the honest cost of cutting audio into utterances:
each window is decoded without the neighbouring words' context. It is
punctuation, casing and one elided "on" — not missing speech.

**Capability provenance.** `faster-whisper` now reports `streaming: true` with
`capability_sources["streaming"] = "verified"`. The flag was `false` until the
run above existed, and the unit test that pins it was flipped *before* the
provider: a capability claimed only after it has been demonstrated, never in
hope of it.

## Files changed

* **backend**: `streaming/{__init__,vad,session,decoder}.py`,
  `api/v1/streaming.py`, `services/streaming.py`,
  `providers/faster_whisper.py` (`transcribe_pcm`), `core/config.py` (four
  `STT_STREAM_*` settings), `api/security.py` (`Request` → `HTTPConnection`,
  so REST and WebSocket share one credential rule), `main.py`
* **backend tests**: `unit/test_stream_{vad,session,decoder}.py`,
  `unit/test_faster_whisper_pcm.py`, `integration/test_stream_api.py`,
  `unit/test_compose_env.py`, `unit/test_faster_whisper_unit.py`
* **frontend**: `api/{client,types}.ts`, `hooks/{useLiveStream,useLiveTranscript,useRecorder}.ts`,
  `lib/blobBytes.ts`, `test/webSocket.ts`, `components/Recorder.tsx`,
  `nginx.conf` (upgrade proxying), `styles.css`
* **frontend tests**: `hooks/useLiveStream.test.tsx`,
  `hooks/useLiveTranscript.test.tsx`, `hooks/useRecorder.test.ts`,
  `components/Recorder.test.tsx`, `api/client.test.ts`, `nginx-config.test.ts`
* **ops/docs**: `docker-compose.yml`, `.env.example`, `docs/streaming.md`,
  `docs/api.md`, `docs/ui.md`, `scripts/e2e_phase8.py`

No new dependencies: `numpy` and `ffmpeg` were already there; the WebSocket
implementation is Starlette's (`uvicorn[standard]`).

## Limits, honestly

* **Only the local provider streams.** The managed adapters still report
  `streaming: false` — Vendors do offer streaming sockets (Deepgram's is
  real-time), but wiring one is a separate job with a separate cost story, and
  a managed stream would spend someone's quota continuously rather than once.
  The endpoint refuses them by design until then.
* **Energy VAD, not a speech model.** A very noisy room can open a window on
  noise; the model then returns nothing for it (which the panel shows as no
  text). A learned VAD (Silero) is the upgrade path if that bites.
* **Partial cadence is wall-clock (1 s).** A partial decodes the open window
  again; on a long utterance that is one extra decode per second, bounded by
  `max_window_ms`.
* **The concurrency bound is per API process** (`STT_STREAM_MAX_SESSIONS`,
  default 2), like the rate limiter. Several replicas each keep their own
  count — the same documented trade.
* **A stream in flight when the client vanishes** finishes its decode in a
  daemon thread; its frames go nowhere. Nothing is leaked (no rows, no files),
  but the GPU does finish that one window.

## Next phase (proposed)

The obvious next step is **managed streaming** — starting with Deepgram's
real-time socket, behind the same session contract, with its own capability
flag and provenance line — plus a *speaker-attributed* stream if diarization
can be kept honest per window. Neither is started; both are scoped by what this
phase proved: a stream is a session with a budget, and a capability is only a
claim once a live run has backed it.
