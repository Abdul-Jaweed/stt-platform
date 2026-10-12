# Phase 8 — Streaming transcription (local-first)

Status: **complete** (T1–T8; see `docs/phase-8-report.md` for what ran)

Spec: `IDEA.md` lists `streaming` among the capabilities an adapter must
*represent* (never fabricate), and voice agents among the target consumers.
The platform's request/response surface (sync `201`, durable `202`, the
chunked live preview) is complete; this phase adds the one transport it
deliberately did not have: a persistent session that returns text **while
the caller is still speaking**.

Nothing in the spec mandates this transport, so the scope decisions below
are the phase's real content.

## Design decisions

1. **The stream is a session, not a job.** `WS /v1/transcriptions/stream`
   receives audio and returns text over one connection. No job row, no
   stored object, nothing persisted — the durable artefact is still the
   recording that goes through `POST /v1/transcriptions` when the caller
   stops. This mirrors the preview's contract, for the same reason.
2. **Local-first; managed providers are refused in v1.** A managed start
   answers an error event `STREAM_LOCAL_ONLY` (the sibling of
   `LIVE_PREVIEW_LOCAL_ONLY`): a per-minute billed socket held open for
   minutes is exactly the spend the free-tier rule forbids by default.
   A provider's `streaming` capability flag flips to `true` only when a
   real stream is verified live — nothing is advertised early.
3. **Container in, PCM out, one decoder per session.** The browser sends
   the bytes `MediaRecorder` already produces (WebM/Opus slices). The
   server pipes them into one long-lived ffmpeg child (`stdin` = container
   bytes, `stdout` = 16 kHz mono s16le PCM). The session core never sees a
   container, which is what makes it unit-testable with synthetic frames —
   and it stops the O(n²) re-decode the chunked preview pays: a stream
   decodes every byte once.
4. **Energy-based VAD splits speech; Whisper stays a batch model.** A
   dependency-free RMS gate with hangover (`min_speech_s`, `silence_s`)
   decides where an utterance ends. Feeding windows and closing one on
   silence is how a batch model is streamed honestly. Partials are
   re-decodes of the still-open window and are labelled `is_final: false`,
   because they can still change.
5. **One decode at a time; ticks are dropped, never queued.** The session
   holds a single in-flight decode and refuses to stack work — the same
   rule the preview hook learned on real audio.
6. **Offsets are session-absolute.** Every segment carries timestamps on
   the stream's own timeline, counted from the first byte received, so a
   client can join finals to the recording it is making without arithmetic.
7. **Bounds are configuration; the cap is an event, not a surprise.**
   `STT_STREAM_MAX_DURATION_S` (default 300) closes the session with
   `capped` then `done`. `STT_STREAM_MAX_SESSIONS` (default: the GPU
   concurrency) refuses excess sessions with `STREAM_BUSY` rather than
   letting decodes thrash the GPU. Sessions idle past
   `STT_STREAM_IDLE_TIMEOUT_S` are closed.
8. **The last test is the honest one.** A GPU run streams the same audio
   the batch path transcribes and asserts the finals agree with the batch
   transcript — streaming earns its claim the same way every other
   capability on this platform did.

## Protocol

Client → server (text frames are JSON):

| Frame | Payload | Meaning |
|---|---|---|
| `start` | `{"type":"start","language"?,"encoding"?:"container"\|"pcm16","sample_rate"?}` | opens the session; `pcm16` must state `sample_rate`, mono s16le |
| binary | audio bytes | appended to the decoder |
| `stop` | `{"type":"stop"}` | flush, emit remaining finals, `done` |

Server → client (JSON text frames):

| Event | Payload | Meaning |
|---|---|---|
| `ready` | `provider`, `model`, `session_id`, `max_duration_s`, `partial_interval_s` | the session is live; decode starts |
| `partial` | `text`, `segments[]`, `window:{start_s,end_s}`, `is_final:false` | the open window, re-decoded; may change |
| `final` | `text`, `segments[]` (closed, session-absolute timestamps) | one utterance is closed and will not change |
| `capped` | `max_duration_s` | the cap was reached; audio after this is refused |
| `error` | `code`, `message` | `STREAM_LOCAL_ONLY`, `STREAM_BUSY`, `STREAM_BAD_REQUEST`, `STREAM_DECODE_FAILED`, `STREAM_INFERENCE_FAILED`, `VALIDATION_ERROR` |
| `done` | `duration_s`, `text` | the session ended; the connection closes |

## Tasks

- **T1 — Windowing + VAD** (`streaming/vad.py`, `streaming/window.py`):
  PCM16 frames → float windows, RMS gate with hangover, utterance open /
  close decisions. Unit tests with synthetic frames (tone vs silence) and
  odd-sized frames.
- **T2 — Session core** (`streaming/session.py`): feed/finalize/cap/idle,
  partial cadence, single in-flight decode, session-absolute offsets,
  nothing persisted. Unit tests with a fake transcriber, including the
  "slow decode must not stack" case.
- **T3 — Decoder** (`streaming/decoder.py`): one ffmpeg child per session,
  container → PCM; raw `pcm16` passthrough; corrupt input fails as
  `STREAM_DECODE_FAILED` without killing the API process. Tests synthesize
  WebM/WAV with the existing fixtures.
- **T4 — Endpoint** (`api/v1/streaming.py`): the protocol above, auth via
  the same scope dependency as the rest of `/v1`, local-only refusal,
  session registry with limits, clean close on client disconnect.
  Integration tests with `TestClient.websocket_connect` and a fake
  transcriber.
- **T5 — Local decode plumbing**: `FasterWhisperProvider.transcribe_pcm()`
  (a 16 kHz float window through the same engine and semaphore); capability
  honesty stays `streaming: false` until T8's live run.
- **T6 — Console**: `useLiveStream` (WebSocket, `MediaRecorder` timeslice
  slices, partials rendered live), recorder wiring with fallback to the
  existing preview when the socket cannot be used, nginx upgrade support.
- **T7 — Docs + config**: `docs/streaming.md`, `api.md`, `README.md`,
  compose forwarding for the `STT_STREAM_*` knobs.
- **T8 — End-to-end**: stream `audio/sample.wav` through the real stack and
  assert the finals agree with the batch transcript; then the console.

## Non-goals

- Word-level partials (Whisper re-decodes windows; words settle at `final`).
- Diarization, translation, or any non-STT model.
- Cloud streaming sessions (the flag and the refusal ship; the adapters do
  not, until a vendor stream is verified live like everything else).

## Delivered — and where it differs from this plan

All eight tasks landed; the report (`docs/phase-8-report.md`) carries the
evidence. The differences worth recording, because a plan that quietly
disagrees with the code is worse than no plan:

- **The start frame's field is `input_format`** (`"container"` | `"pcm16"`), not
  `encoding`, and the rate is fixed at 16 kHz rather than client-declared: the
  decoder always resamples, so a client-stated rate would have been a claim the
  server ignores.
- **`done` carries a `reason`** (`stop` | `capped` | `idle`). The plan only had
  the cap as an event; an idle ending and a client stop are useful to tell
  apart, and the console shows the difference.
- **Two more error codes than listed**: `STREAM_MALFORMED_TRANSCRIPT` (a
  provider returned something the canonical schema cannot represent — reported,
  never silently dropped, which is the batch path's rule too) and
  `STREAM_INTERNAL`. `VALIDATION_ERROR` did not appear: a bad start frame is
  `STREAM_BAD_REQUEST`, since there is no request body to validate.
- **`STT_STREAM_MAX_SESSIONS` defaults to 2**, not to the GPU concurrency, and
  `STT_STREAM_MAX_FRAME_BYTES` joined the list. A stream holds a GPU slot for
  its whole life, so the bound wants to be lower than the batch concurrency,
  and one unbounded frame is an easy way to make the server buffer for you.
- **"One decode at a time" needed no in-flight flag.** The session is
  synchronous and the cadence gate (`partial_interval_s`) decides when a
  partial is due, so stacking is not possible rather than prevented. The rule
  the plan was protecting is enforced by construction.
- **The fallback to the chunked preview lives in `useLiveTranscript`**, not in
  `useLiveStream`. The transport reports `unsupported`; the policy decides what
  that means. It also decides the thing the plan did not specify: the fallback
  runs **only when nothing ever arrived from the server**, never on top of text
  already on screen.
- **`streaming/vad.py` is both windowing and VAD**; a separate
  `streaming/window.py` would have held one dataclass.
- **Finals carry word timings** (`word_timestamps=True` on the window decode),
  so the non-goal "word-level partials" still holds while finals are as rich as
  the batch path's segments.
