# Live streaming transcription

A WebSocket endpoint that transcribes audio **while it is being spoken**, on
the local model, without persisting anything.

```
ws://<host>/v1/transcriptions/stream
```

Why a stream and not a job: the console records in the browser, and the person
talking wants to see words appear. A job is the right shape for work that must
be durable and retryable — a stream is the right shape for text that is useful
*now*. Both exist, and the recording still travels the job path when you press
Stop: **streaming never replaces durable transcription, it precedes it.**

## The shape of a session

The client connects, sends a `start` frame, then audio, then asks to stop:

```jsonc
// client -> server, once (optional: audio may arrive first)
{ "type": "start", "provider": "faster-whisper", "language": "en",
  "input_format": "container" }        // "container" (default) or "pcm16"
```

```jsonc
// server -> client
{ "type": "ready", "provider": "faster-whisper", "model": "large-v3-turbo",
  "session_id": "…", "max_duration_s": 300, "partial_interval_s": 1.0,
  "sample_rate": 16000, "input_format": "container" }

{ "type": "partial", "text": "hello wor", "is_final": false,
  "segments": [ … ], "window": { "start_s": 0.0, "end_s": 2.4 } }

{ "type": "final", "text": "hello world", "segments": [ … ],
  "window": { "start_s": 0.0, "end_s": 2.9 } }

{ "type": "capped", "max_duration_s": 300 }        // the cap was reached
{ "type": "done", "reason": "stop", "duration_s": 12.4, "text": "hello world" }
{ "type": "error",  "code": "STREAM_BUSY", "message": "…", "fatal": true }
```

* Binary frames are audio. Anything the browser's `MediaRecorder` produces
  works: one long-lived `ffmpeg` child per session probes the container and
  decodes it once (`webm/opus` included). A client that already has raw mono
  `s16le` can say `input_format: "pcm16"` and skip the subprocess.
* **`partial` can change; `final` cannot.** A final is an utterance that the
  voice-activity segmenter closed and the model decoded exactly once. Text in
  the panel is shown in two weights for that reason, not for decoration.
* Timestamps are stamped on the **session's** timeline (the recording the
  client is making), not the window's, so a final can be joined straight back
  to the audio.
* `{"type": "stop"}` asks the server to flush the open utterance and finish;
  `done.text` is the authoritative concatenation of the finals.

## The rules it keeps

* **Local model only.** Asking for a managed provider is refused with
  `STREAM_LOCAL_ONLY` *before a byte of audio is accepted*. Local audio never
  gets routed to a cloud vendor by surprise — send the recording through
  `POST /v1/transcriptions` and let the job choose its provider.
* **Nothing is persisted.** No job row, no object, no trail. What the stream
  hears is not the transcript; the durable artefact is the upload.
* **The tenant comes from the server.** The same credential as the REST API:
  the console's `HttpOnly` session cookie, or an API key (`X-API-Key` /
  `Authorization: Bearer`). A missing or invalid credential fails the
  handshake (HTTP 403); nothing the client sends can pick a tenant.
* **Bounded, and honest about it.** `STT_STREAM_MAX_DURATION_S` (default 300)
  closes a long session with `capped` then `done`; `STT_STREAM_IDLE_TIMEOUT_S`
  (default 30) ends a session that hears nothing with `done: idle`;
  `STT_STREAM_MAX_SESSIONS` (default 2, per API process) answers extra callers
  with `STREAM_BUSY` rather than queueing them behind a GPU slot.
* **A failure is a message, not a crash.** A provider error mid-session is an
  `error` frame (`STREAM_INFERENCE_FAILED`) and the session keeps going.

## Error codes

| code | fatal | meaning |
| --- | --- | --- |
| `STREAM_LOCAL_ONLY` | yes | a managed provider was requested |
| `STREAM_BAD_REQUEST` | yes | bad start frame, unknown provider, unknown `input_format`, a provider without a window API, an oversized frame |
| `STREAM_BUSY` | yes | the process is already at `STT_STREAM_MAX_SESSIONS` |
| `STREAM_DECODE_FAILED` | yes | the bytes were not audio (nothing could be decoded) |
| `STREAM_INFERENCE_FAILED` | no | the model failed on one window; the session continues |
| `STREAM_MALFORMED_TRANSCRIPT` | no | the provider returned something the canonical schema cannot represent (reported, never silently dropped) |
| `STREAM_INTERNAL` | yes | a bug — logged with a traceback, reported opaquely |

## How the server runs it

```
socket ──receive loop──► decoder (ffmpeg child) ──PCM──► session (VAD +
                                                        model) ──frames──► queue ──send loop──► socket
```

The event loop owns the socket; one worker thread owns the session. The worker
publishes frames onto an asyncio queue with `call_soon_threadsafe`, so the
session stays a plain synchronous object with no locks in it. Inference goes
through the provider's own GPU semaphore, so a stream waits for a batch job on
the same GPU instead of racing it.

The segmenter is energy-based on the *stream* (a second VAD in the provider
would re-cut a window the segmenter deliberately kept whole). Windows close on
trailing silence, roll over at the maximum window length without gaps, and a
window's samples are decoded exactly once.

## Console behaviour

The recorder panel tries the stream first and falls back to the older chunked
preview (`POST /v1/transcriptions/preview`) **only if the socket could not be
opened at all** — never on top of text the user is already reading, because a
fallback there would silently replace the answer rather than improve it.

## Verifying it

```bash
# The end-to-end agreement check: stream a real recording through nginx
# (the console's own path), then transcribe the same audio as a job and
# compare. Needs ./launch.sh up; run it in the foreground.
cd backend && uv run python ../scripts/e2e_phase8.py
```

Unit and integration coverage:

```bash
cd backend
uv run pytest tests/unit/test_stream_vad.py tests/unit/test_stream_session.py \
              tests/unit/test_stream_decoder.py tests/unit/test_faster_whisper_pcm.py -q
uv run pytest tests/integration/test_stream_api.py -q
```

Capability provenance is a claim about verification: `faster-whisper` reports
`streaming: true` only because `scripts/e2e_phase8.py` ran against the real
stack and the streamed text agreed with the batch transcript. Until such a run
happened, the flag was `false` and the console would have had nothing to talk
to — the honest state, not a hopeful one.
