# STT Platform — Frontend

Standalone React + TypeScript + Vite app. Talks to the backend **only**
through the documented `/v1` HTTP API (no Python imports, no provider SDKs,
no secrets). Spec: `../IDEA.md` §Separate frontend.

## Commands

```bash
npm install
npm test              # vitest + testing-library + msw (no backend needed)
npm run build         # tsc --noEmit && vite build
npm run dev           # dev server (http://localhost:5173)
npm run preview       # serve the built app (http://localhost:4173)
```

The API base URL comes from `VITE_API_BASE_URL` (default
`http://127.0.0.1:8200` — always 127.0.0.1, never localhost: Windows
`localhost` resolves to `::1` and hangs).

The backend must allow the frontend origin via CORS: dev (5173) and preview
(4173) are allowed by default (`STT_CORS_ORIGINS` overrides).

## Pages

- `/` — upload: file picker, provider + language selection (providers come
  from `/v1/providers`), idempotency key per submission. `201` (sync) and
  `202` (async) both navigate to the job page.
- `/jobs/:id` — status polling (stops on terminal states), failure panel,
  cancel (non-terminal) and retry (retryable failures) actions.
- `/jobs/:id/transcript` — timestamped transcript, word rows when the
  result's granularity is `word`, click-to-seek playback (when the uploaded
  file is still retained in this tab), TXT/SRT/VTT downloads.
- `/providers` — capability + health table from `/v1/providers`.
- `/evaluations` — placeholder that states the Phase-6 gap honestly on 404.

## Design notes / limitations (honest)

- **No job list exists in `/v1`** — the app keeps its own recent-jobs index
  in localStorage (`stt.recentJobs`, best-effort, cap 20). A server-side
  list endpoint is a Phase-4+ decision.
- **Playback is tab-local**: the uploaded `File` is retained in memory
  (`src/lib/retainedAudio.ts`) so the transcript page can play it. After a
  reload the transcript still renders and playback is marked unavailable —
  the API serves no audio stream yet.
- **Polling only** (no SSE/WebSockets) per spec; interval 2 s, stops at
  terminal states.
- Exports are generated client-side from the result JSON; exports use
  segment timings (word granularity is display-only).
- `404` from `/v1/evaluations/{id}` renders the Phase-6 notice — nothing is
  invented to fill the gap.
