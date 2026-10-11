# The frontend

A React + TypeScript + Vite app that consumes **only** the platform's `/v1`
API. It holds no provider logic, no keys, and no database access: replacing the
backend with anything that speaks the same contract leaves the UI untouched.

```
frontend/
├── src/
│   ├── api/           client.ts (fetch + error envelope), context.tsx, types.ts
│   ├── components/    Layout, Recorder, JobStages, StatusBadge, TranscriptView
│   ├── hooks/         useRecorder.ts
│   ├── lib/           format.ts, subtitles.ts, retainedAudio.ts
│   ├── pages/         UploadPage, JobPage, TranscriptPage, ProvidersPage, EvaluationsPage
│   └── styles.css     design tokens + components
└── src/test/          msw server, recorder stand-ins, setup
```

## Screenshots

`docs/images/` holds live captures of every page (upload/recorder, recorder
preview, job tracker, transcript, providers, evaluations); the README embeds
the main ones and `ui-recorder-preview.png` shows a finished take before it is
submitted.

## Pages

| Route | What it does |
|---|---|
| `/` | Upload a file **or record from the microphone**, pick provider/language, submit |
| `/jobs/:id` | Lifecycle tracker, status, provider, model, attempts, timings; retry/cancel |
| `/jobs/:id/transcript` | Segments and words with playback sync, copy, TXT/SRT/VTT export |
| `/providers` | Capability matrix reported by the API (never hard-coded) |
| `/evaluations` | Run an evaluation, compare runs (WER/CER/RTF as numbers *and* meters) |

## Recording in the browser

`useRecorder` wraps `MediaRecorder` and exposes the states the UI needs:
`idle → requesting → recording → ready` (or `error`).

- **Permission is explicit.** A denial, a missing device, or a device in use
  each produce a readable message — never a crash and never a silent no-op.
- **The microphone is never left hot.** Tracks are stopped on stop, on discard,
  and on unmount. `reset()` revokes the previous object URL.
- **The level meter is best-effort.** If `AudioContext` is unavailable the
  meter stays at zero; recording is unaffected.
- **The clip is a `File`.** It travels the same upload path as a chosen file
  (same multipart field, same idempotency key, same retention), so a recorded
  take is not a second-class citizen.
- **Unsupported browsers** (no `MediaRecorder`/`getUserMedia`) get a clear
  statement in the *Record audio* tab instead of a dead button.

Container choice follows the browser: Opus/WebM where supported, falling back
to Ogg or MP4, and the file extension is derived from the MIME type that was
actually used.

### Streaming WebM has no duration — and that is fine

`MediaRecorder` writes a *streaming* WebM: the Segment Info carries no Duration
element, so `ffprobe` reports no duration at all for a perfectly valid
recording. The platform therefore **measures** instead of guessing or refusing:
when `format.duration` is absent, the backend decodes the stream
(`ffmpeg -f null -progress pipe:1`) and takes the last `out_time_us` as the
duration (`audio/inspect.py::_measure_duration_s`). That value then feeds the
sync/async decision and the job's recorded audio metadata like any other.

This is provider-neutral: any streaming container (WebM, Matroska, live MP4)
benefits, and the honest alternative — assuming a duration from the bitrate or
the file size — is never used.

## Tests

`npm test` runs Vitest + Testing Library against a mocked API (msw).

- `src/hooks/useRecorder.test.ts` — permission denial, ticking timer, blob
  assembly, microphone release, level-meter fallback.
- `src/components/Recorder.test.tsx`, `JobStages.test.tsx` — panel behaviour and
  the lifecycle tracker's honest handling of failed/cancelled jobs.
- Page tests — upload (file, drop, record), job, transcript (copy, empty
  transcript), providers, evaluations (numbers *and* meters).

jsdom ships neither `MediaRecorder` nor `getUserMedia`; controllable stand-ins
live in `src/test/mediaRecorder.ts`. jsdom's `Blob` has no `.text()` — read
blobs with `FileReader` in tests.

## Design

`styles.css` opens with tokens (surfaces, text, accents, radii, shadows, fonts)
and then styles components in one pass. Two rules of the house:

1. **Status is never carried by colour alone** — badges pair a dot with the
   status word, meters sit beside their number.
2. **Missing data says so.** An empty transcript explains itself; a metric that
   was not measured renders as "not reported", never as a zero-length bar.
