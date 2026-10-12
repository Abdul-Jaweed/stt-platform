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

`docs/images/` holds live captures taken from a real browser against the real
backend:

| File | Shows |
|---|---|
| `ui-landing-dark.png`, `ui-landing-light.png` | the public marketing page (dark / light) |
| `ui-landing-mobile.png` | the same page at 390 px |
| `ui-login-dark.png` | sign-in form |
| `ui-upload.png` | upload tab, dark |
| `ui-recorder.png`, `ui-recorder-preview.png` | recording, and a finished take before it is submitted |
| `ui-live.png`, `ui-live-light.png` | live transcription while recording (dark / light) |
| `ui-transcript.png`, `ui-transcript-light.png` | the transcript page (dark / light) |
| `ui-job.png`, `ui-job-light.png` | lifecycle tracker and job metadata |
| `ui-providers.png`, `ui-evaluations.png` | capability matrix, evaluation comparison (summary cards, search, sortable columns, detail card) |
| `ui-mobile.png` | 390 px viewport, light theme |

All of the above were captured against the **Docker stack** (`./launch.sh`),
served by nginx on `:8080` — the same bundle a deployment ships.

## Theme

Light and dark are the same token set, re-pointed (`:root` for dark,
`:root[data-theme="light"]` for light); no component knows which is active.
`useTheme` owns the rule:

- an explicit choice (localStorage `stt.theme`) beats the operating system,
  which beats the dark default;
- while no choice has been made, a system change is followed live;
- the choice is applied **before first paint** — `index.html` carries a tiny
  inline script with the same rule, so a light-mode user never sees a dark
  flash — and `main.tsx` re-applies it, keeping both in lockstep
  (`src/shell.test.ts` asserts the key and the attribute stay in sync);
- `color-scheme` is set alongside `data-theme`, so native controls (audio
  player, selects, file picker, scrollbars) match without CSS hacks.

The bulb in the header is the only switch: outline in dark (light is what you
would switch *to*), lit in light. Its accessible name states the action and
`aria-pressed` states the position — colour is never the only signal.

## Icons

`public/favicon.svg` is the app mark (a waveform in a rounded gradient
square); `favicon-32.png` and `apple-touch-icon.png` are generated from the
same geometry, and `manifest.webmanifest` lists all three. The header renders
the identical SVG (`components/BrandMark.tsx`), so the tab and the app cannot
drift apart.

Every icon in the interface is a [Phosphor](https://phosphoricons.com) icon
(`@phosphor-icons/react`), imported per icon so the bundle only carries what it
uses. `src/icons.test.ts` enforces the rule across the source tree: no emoji
and no stray Unicode glyphs standing in for icons — a character that renders
differently on every platform is not an icon. It also asserts that icons used
as decoration are `aria-hidden`, and that interactive controls keep an
accessible name.

## Signing in

`/` and `/login` are public; every console route sits behind `RequireAuth`,
which asks the API once (`GET /v1/auth/me`) and renders nothing until it knows
the answer — a guarded page never flashes content it may not be allowed to
show.

- The browser holds **no secret**: `POST /v1/auth/login` sets an `HttpOnly`,
  `SameSite=Lax` session cookie, so no token is readable from JavaScript and
  nothing is kept in `localStorage`.
- The **server** decides which tenant the session acts as. Nothing the client
  sends can change the scope, exactly as with an API key.
- Sign-out navigates away from the guarded route *before* dropping the
  session, so the guard cannot race the redirect.
- API keys keep working alongside cookies: the console is one client among
  others.

The login page asks `GET /v1/auth/config` before it claims anything. When the
server reports **demo mode** (and only then), it renders a panel with a single
**Use demo credentials** button: the username, plus the password *if* the
deployment still runs on the shipped development default. On a deployment with
a generated password the button fills the username only and the copy names
`STT_UI_PASSWORD` in `.env` — the value is not sent to the browser, so it
cannot be filled in for you. Nothing is assumed: an unreachable config endpoint
means no claim and no panel. See `docs/deployment.md` for the switch.

## Live transcription

While the microphone is open, the panel shows what the local model is hearing.
`useLiveTranscript` uses the **WebSocket stream**
(`WS /v1/transcriptions/stream`, see `docs/streaming.md`) and falls back to the
older chunked preview (`POST /v1/transcriptions/preview`) only when a socket
cannot be opened at all — never on top of text the reader can already see,
because that would silently swap the answer instead of improving it.

- **Two weights, on purpose.** Text the server has closed (a `final`) is shown
  normally; the utterance still open (a `partial`) is lighter, because it can
  still change. A final never does.
- **Local model only.** The backend refuses a managed provider with
  `STREAM_LOCAL_ONLY` (and `LIVE_PREVIEW_LOCAL_ONLY` on the fallback); the panel
  says so in words.
- **Nothing is persisted.** No job, no object, no trail — the recording you
  stop is what gets transcribed durably, through the same upload path as a
  chosen file.
- **A live failure never touches the recording.** The panel reports it and the
  take carries on; the submit path is unchanged.
- **Sends never stack or repeat.** Each recorder chunk is sent exactly once, in
  order (chunk boundaries are byte-exact, so a re-send would corrupt the
  stream); the socket is bounded by the server's cap, which answers `capped`
  and `done` rather than growing forever.

## Transcript

The transcript is the hero of the page and is laid out as a reading surface:
a monospaced time gutter you can click to seek, the segment text at the
largest size, and word chips below it that stay quiet until you look at them.
The active segment and the active word are marked on the element, so playback
position is visible at a glance. Per-segment copy, one-click copy of the whole
text, and TXT/SRT/VTT export sit in the toolbar; a summary line states the
segment count, the audio duration and the timestamp granularity actually
present.

## Pages

| Route | What it does |
|---|---|
| `/` | The marketing page (public): what the platform does, how a job runs, the providers, and how to run the whole thing yourself |
| `/login` | Sign in; sets the session cookie |
| `/upload` | Upload a file **or record from the microphone**, pick provider/language, submit |
| `/jobs/:id` | Lifecycle tracker, status, provider, model, attempts, timings; retry/cancel |
| `/jobs/:id/transcript` | Segments and words with playback sync, copy, TXT/SRT/VTT export |
| `/providers` | Capability matrix reported by the API (never hard-coded) |
| `/evaluations` | Run an evaluation; summary cards, a searchable/sortable comparison table with filters and paging, and a detail card per run (by-group and per-clip tables) |

The evaluations page states what it measured and what it could not: a run whose
WER is unavailable shows `not reported` and is never counted as a best, the
summary's numbers are derived from the runs themselves, and a failed list load
reports itself with a Retry instead of rendering an empty history.

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
  transcript, summary), providers, evaluations (numbers *and* meters).
- `src/lib/theme.test.ts`, `src/hooks/useTheme.test.tsx`,
  `src/components/ThemeToggle.test.tsx` — precedence, persistence, following
  the system only until the user chooses.
- `src/hooks/useLivePreview.test.tsx` — cadence, the minimum-audio wait, the
  cap, no stacking, and a failing preview leaving everything else alone.
- `src/shell.test.ts` — the favicon set, the pre-paint theme boot and the
  theme-colour metas in `index.html`.

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
