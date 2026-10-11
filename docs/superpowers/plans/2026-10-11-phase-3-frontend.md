# Phase 3 — Separate Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone React + TypeScript + Vite app that consumes only the documented `/v1` API: upload + configure, watch jobs (polling), review transcripts with timestamped playback and downloads, inspect provider capabilities.

**Architecture:** Independent app under `frontend/` with its own package.json, tests, and commands (spec: separate applications). A typed fetch client mirrors the backend schemas by hand (no codegen — the contract is stable and small). Pages via `react-router-dom`. State stays local: React hooks + a localStorage "recent jobs" index (the API has **no job-list endpoint** — documented limitation). Polling (not SSE/WebSockets) per spec. No provider SDKs, no secrets, no Python imports.

**Tech Stack:** React 19 + TypeScript + Vite 7, `react-router-dom` 7, Vitest 3 + Testing Library + jsdom + MSW 2 (API mocked at the network layer), npm with committed `package-lock.json`.

**Spec:** `I:\Transcription-Pipeline\IDEA.md` §Separate frontend, §Headless API, §Definition of done; backend contract as built: `docs/api.md`, `docs/reliability.md`.

## Global Constraints

- The frontend must **never** import Python modules or call provider SDKs; only `/v1` over HTTP.
- Never put managed-provider secrets in the frontend (there are none to begin with — the API holds them server-side).
- API base URL from `VITE_API_BASE_URL` (default `http://127.0.0.1:8200`); **127.0.0.1, never localhost** (Windows `::1` hang).
- Polling only (no SSE/WebSockets this phase).
- Errors surface the backend's `{"error": {"code", "message"}}` shape; no invented messages.
- Every async view has loading / empty / error / retry states (spec §Separate frontend).
- Word timestamps render only when `timestamp_granularity == "word"` and words exist — **never fabricate** timestamps the API didn't return.
- The evaluations view targets `GET /v1/evaluations/{id}` which does **not exist yet** (Phase 6): render a honest "not available yet" state on 404, no fake data.
- Tests mock the network with MSW; no test may require the backend or the compose stack.
- `npm` only; commit `package-lock.json`; `node_modules/` gitignored.

## Review Focus

1. **Backend returns 202 (async job) from upload** — UI must switch to the polling view, not treat 202 as an error and not block the request waiting for a result.
2. **Backend returns 201 with status FAILED** (sync validation/provider failure) — UI must show the failure (code/message/retryable) and offer retry when `failure.retryable`.
3. **Polling hits a terminal state** — polling must stop at `SUCCEEDED`/`FAILED`/`CANCELLED` (no infinite interval), and a job that 404s (deleted/expired) must render, not crash.
4. **Result contains segment-only timestamps** (no words) — viewer must render segments without word rows, and SRT/VTT export must use segment timings.
5. **Large transcript** (hundreds of segments) — rendering must not freeze (virtualization is overkill; cap what renders only if measured slow — assert scroll/select behavior on a 500-segment fixture instead).

---

### Task 1: Scaffold + toolchain (Vite, TS, Vitest, RTL, MSW)

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/App.test.tsx`, `frontend/src/test/setup.ts`, `frontend/src/vite-env.d.ts`
- Modify: `.gitignore` (add `node_modules/`, `frontend/dist/`)

**Interfaces (produces):** `npm test` (vitest run) and `npm run build` (tsc -b && vite build) work; `App` renders an app shell with heading "STT Platform".

- [x] **Step 1:** write `package.json` with scripts (`dev`, `build`, `test`, `test:watch`, `preview`) and deps: `react`, `react-dom`, `react-router-dom`; devDeps: `typescript`, `vite`, `@vitejs/plugin-react`, `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`, `@testing-library/user-event`, `msw`. `npm install` → commit `package-lock.json`.
- [x] **Step 2:** write `App.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import App from "./App";

test("renders the app shell", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: /stt platform/i })).toBeInTheDocument();
});
```

- [x] **Step 3:** `npm test` → FAIL (no App / no test setup). Write `vite.config.ts` (react plugin, `test: { environment: "jsdom", setupFiles: "./src/test/setup.ts", globals: true }`), `tsconfig.json` (strict), `index.html`, `src/test/setup.ts` (`import "@testing-library/jest-dom/vitest"`), minimal `App.tsx`.
- [x] **Step 4:** `npm test` → PASS; `npm run build` → PASS. Commit `feat(frontend): vite + react + ts scaffold with vitest (T1)`.

### Task 2: Typed API client + error normalization

**Files:**
- Create: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/api/errors.ts`, `frontend/src/api/client.test.ts`
- Test: MSW server in `src/test/server.ts` (shared), handlers per test

**Interfaces (produces):**
- `types.ts`: `JobStatus` union (`ACCEPTED|VALIDATING|STORED|QUEUED|PROCESSING|SUCCEEDED|RETRY_WAIT|FAILED|CANCELLED`), `JobFailure {code, message, retryable, provider_code?}`, `AudioMetadata`, `JobRecord` (id, status, requested_provider?, actual_provider?, model?, language_requested?, language_detected?, audio?, original_object_key?, attempt_count, failure?, created_at, updated_at), `TranscriptWord {start, end, text, confidence?}`, `TranscriptSegment {start, end, text, words?}`, `TranscriptResult {job_id, text, language?, segments, timestamp_granularity: "word"|"segment", audio_duration_s, processing_duration_s, provider, model?, model_version?}`, `ProviderInfo {id, name, is_local, capabilities, health}`.
- `errors.ts`: `ApiError extends Error { status: number; code: string }`; `normalizeError(status, body)` reads `body.error.{code,message}`, falls back to `UNKNOWN_ERROR`.
- `client.ts`: `createApiClient(baseUrl)` → `{ createTranscription({file, provider?, language?, idempotencyKey?}) -> {status: 201|202, job: JobRecord}`, `getJob(id)`, `getResult(id)`, `retry(id)`, `cancel(id)`, `getProviders()`, `getHealth()`, `getEvaluation(id)` }`. Multipart body; JSON for the rest; `Idempotency-Key` header pass-through.

**Tests (MSW):**
```ts
test("createTranscription posts multipart and returns 201 job")
test("createTranscription returns 202 without treating it as an error")
test("error body is normalized into ApiError with code and message")
test("network failure becomes ApiError with code NETWORK_ERROR")
test("getEvaluation surfaces 404 as a typed not_found")
```

- [x] Steps: write tests → RED (`npm test` fails on missing modules) → implement → GREEN → commit `feat(frontend): typed api client and error normalization (T2)`.

### Task 3: Upload form with provider/language selection

**Files:**
- Create: `frontend/src/pages/UploadPage.tsx`, `frontend/src/pages/UploadPage.test.tsx`, `frontend/src/components/StatusBadge.tsx`, `frontend/src/lib/format.ts`
- Modify: `App.tsx` (router: `/` = upload, later pages)

**Interfaces:** consumes `createApiClient` + `getProviders`; produces `UploadPage` that: selects a file (`<input type="file" accept="audio/*,video/webm">`), loads providers on mount (loading/error states), lets the user pick provider (only when >1) and language (free text, optional), submits with an `Idempotency-Key` (crypto.randomUUID per submission), then navigates to `/jobs/:id` on 201/202; 4xx errors render `ApiError.code`/message inline; `format.ts` provides `formatDuration(s)`, `formatBytes(n)`, `formatTimestamp(s)` (mm:ss.mmm).

**Tests:**
```ts
test("shows providers from the api, including local badge")
test("submits the chosen file and navigates to the job page")
test("renders validation errors from the backend (415 UNSUPPORTED_MEDIA)")
test("renders provider list failure with retry affordance")
```

- [x] Steps as usual; commit `feat(frontend): upload form with provider and language selection (T3)`.

### Task 4: Job page — polling, status, cancel/retry

**Files:**
- Create: `frontend/src/pages/JobPage.tsx`, `frontend/src/pages/JobPage.test.tsx`, `frontend/src/hooks/useJobPolling.ts`, `frontend/src/hooks/useJobPolling.test.ts`, `frontend/src/lib/recentJobs.ts`, `frontend/src/lib/recentJobs.test.ts`
- Test: fake timers (`vi.useFakeTimers`) for polling

**Interfaces:**
- `useJobPolling(client, id, {intervalMs=2000})` → `{job, error, loading, refresh}`; polls only while status is non-terminal (`QUEUED|PROCESSING|RETRY_WAIT|ACCEPTED|VALIDATING|STORED`); stops on terminal; 404 → error state.
- `recentJobs.ts`: `addRecentJob({id, filename, createdAt})`, `getRecentJobs()`, cap 20, localStorage key `stt.recentJobs` (safe parse; corrupt JSON → []).
- `JobPage`: status badge (colors per state), metadata (provider/model/duration, `attempt_count`), failure panel (code/message; Retry button when `failure.retryable`, Cancel button while non-terminal), transcript link when SUCCEEDED.

**Tests:**
```ts
test("polls until SUCCEEDED and then stops")            // fake timers; assert no extra fetches after terminal
test("shows failure code and offers retry when retryable")
test("cancel calls the api and shows the new status")
test("unknown job (404) renders a not-found state")
test("recent jobs index survives corrupt localStorage")
```

- [x] Steps as usual; commit `feat(frontend): job page with polling, cancel and retry (T4)`.

### Task 5: Transcript viewer — timestamps, synced playback, downloads

**Files:**
- Create: `frontend/src/pages/TranscriptPage.tsx`, `frontend/src/pages/TranscriptPage.test.tsx`, `frontend/src/components/TranscriptView.tsx`, `frontend/src/components/TranscriptView.test.tsx`, `frontend/src/lib/subtitles.ts`, `frontend/src/lib/subtitles.test.ts`

**Interfaces:**
- `subtitles.ts` (pure): `toPlainText(result) -> string`; `toSrt(result) -> string`; `toVtt(result) -> string`; timestamp format `HH:MM:SS,mmm` (SRT) / `HH:MM:SS.mmm` (VTT); word granularity does **not** change export (segment-level, honest); segments ordered as given.
- `TranscriptView`: props `{result, currentTime, onSeek}` — renders segments with `formatTimestamp`; word rows only when `segment.words?.length` and `result.timestamp_granularity === "word"`; clicking a segment calls `onSeek(start)`; active segment (currentTime within [start,end)) highlighted.
- `TranscriptPage`: loads result (loading/not-ready 409 state with poll hint/error), `<audio controls src=blobUrl>` from the originally selected file (kept in memory via recentJobs store → for reloads without the file, playback is unavailable: honest empty state), download buttons (TXT/SRT/VTT via `URL.createObjectURL`).

**Tests:**
```ts
test("srt output uses segment timings with comma millis")
test("vtt output uses dot millis and WEBVTT header")
test("word rows render only when granularity is word")
test("clicking a segment seeks the audio element")
test("active segment highlights as playback advances")
test("500-segment transcript renders and stays interactive")
```

- [x] Steps as usual; commit `feat(frontend): transcript viewer with synced playback and exports (T5)`.

### Task 6: Providers page + evaluations placeholder + app shell polish

**Files:**
- Create: `frontend/src/pages/ProvidersPage.tsx`, `frontend/src/pages/ProvidersPage.test.tsx`, `frontend/src/pages/EvaluationsPage.tsx`, `frontend/src/pages/EvaluationsPage.test.tsx`, `frontend/src/components/Layout.tsx`
- Modify: `App.tsx` (routes `/`, `/jobs/:id`, `/jobs/:id/transcript`, `/providers`, `/evaluations`, 404 route), `src/main.tsx` (BrowserRouter)

**Interfaces:** `Layout` = header nav (Upload / Providers / Evaluations) + `<Outlet/>`; `ProvidersPage` = capability table (languages count, word timestamps, diarization, streaming, health badge); `EvaluationsPage` = id input → `getEvaluation`; 404 → "Evaluations arrive in Phase 6" empty state (no fake data).

**Tests:**
```ts
test("providers table shows capability flags and health")
test("evaluations view explains the phase-6 gap on 404")
test("unknown route renders not-found")
```

- [x] Steps as usual; commit `feat(frontend): providers, evaluations placeholder, app shell (T6)`.

### Task 7: Styling, build, live e2e against the real backend, docs

**Files:**
- Create: `frontend/src/styles.css`, `frontend/README.md`, `docs/phase-3-report.md`, `scripts/e2e_phase3_frontend.py`
- Modify: `README.md` (frontend quickstart + status), `.gitignore` if needed

**Steps:**
- [x] **Step 1:** minimal dark stylesheet (system font stack, readable tables, status colors; no CSS framework).
- [x] **Step 2:** gates: `npm test` (all suites) + `npm run build`; backend gates re-run (`uv run pytest -q`, `-m gpu`).
- [x] **Step 3 — live e2e:** `scripts/e2e_phase3_frontend.py`: starts backend (uvicorn :8200) + `npm run preview` (built app :4173) with `VITE_API_BASE_URL=http://127.0.0.1:8200`, then drives the real UI (browser automation available to the executor): upload `audio/sample.wav` → 201 → transcript text visible; assert SRT download content; write `results/phase3_e2e/result.json`.
- [x] **Step 4:** `docs/phase-3-report.md` (per-phase format), README updates, tick plan; commit `docs(frontend): phase 3 report and run documentation (T7)`.

## Self-review

- **Spec coverage:** upload + configuration → T3; language/provider selection → T3; job list/status/progress → T4 (local index — no list endpoint exists; documented); transcript display with timestamps → T5; playback synced to segment selection → T5; TXT/SRT/VTT downloads → T5; provider capability display → T6; evaluation results → T6 placeholder (endpoint arrives Phase 6; honest gap); loading/empty/validation/retry/failure states → T3–T6; polling → T4; no secrets in frontend → constraint; no Python imports → constraint.
- **Placeholder scan:** no TBDs; every task names exact files, interfaces, and tests.
- **Type consistency:** `JobRecord`/`TranscriptResult` field names match `docs/api.md` and the backend schemas; `ApiError.code` used uniformly; `useJobPolling` signature single-sourced in T4 and consumed in T4 only.
- **Review Focus:** each of the five items is pinned by a named test in T2 (202), T3 (415), T4 (terminal stop, 404), T5 (segment-only, 500 segments).
