# Phase 3 Report — Separate Frontend

**Status:** complete · **Date:** 2026-10-11 · **Plan:**
`docs/superpowers/plans/2026-10-11-phase-3-frontend.md` (all tasks ticked)

## Objective

Ship the independent React + TypeScript + Vite app the spec calls for
("Separate frontend"): upload + configuration, job visibility (polling),
transcript review with timestamped playback and downloads, provider
capabilities — consuming **only** the documented `/v1` HTTP API, with its
own dependencies, commands, and tests.

## Deliverables

| Task | Deliverable | Commit |
|---|---|---|
| T1 | Vite + React + TS scaffold, Vitest/RTL/MSW harness | `51a2801` |
| T2 | Typed `/v1` API client + error normalization | `73584e8` |
| T3 | Upload form: file, provider (from `/v1/providers`), language, idempotency key | `6e6fdcd` |
| T4 | Job page: status polling, cancel, retry, recent-jobs index | `ccdb35d` |
| T5 | Transcript viewer: word rows, click-to-seek playback, TXT/SRT/VTT | `8305d0c` |
| T6 | Providers table, evaluations placeholder, app shell + routing | `e25aabf` |
| T7 | Backend CORS for browser origins, styles, live e2e, docs | `42411dd` + this commit |

## Backend change required by this phase

The browser runs on its own origin (`:5173` dev / `:4173` preview), so the
API needed CORS — it had none. Added `CORSMiddleware` with
`settings.cors_origins` (default: the two dev origins; `STT_CORS_ORIGINS`
overrides), allow-list headers `Content-Type` + `Idempotency-Key`,
methods GET/POST/OPTIONS. Covered by `tests/integration/test_cors.py`
(preflight allowed / actual request allowed / unlisted origin refused) —
witnessed RED (2 failed) before the middleware landed.

## Dependencies added

`react`, `react-dom`, `react-router-dom`; dev: `typescript`, `vite`,
`@vitejs/plugin-react`, `vitest`, `jsdom`, `@testing-library/react`,
`@testing-library/jest-dom`, `@testing-library/user-event`, `msw`.
`package-lock.json` committed; `npm audit --omit=dev` → 0 vulnerabilities
(remaining advisories are dev-only). Backend: none new.

## Commands executed (real output)

```
frontend: npm test        → 12 files, 44 passed
frontend: npm run build   → tsc --noEmit && vite build ✓ (87.6 kB gzip)
backend:  uv run pytest -q            → 147 passed, 2 deselected
backend:  uv run pytest -m gpu -q     → (run at phase close)
e2e:      uv run python ../scripts/e2e_phase3_frontend.py → PHASE3_API_E2E: OK
```

## Live e2e evidence (`results/phase3_e2e/result.json`)

**API half** (script, against the real stack): `audio/sample.wav` uploaded
with an `Idempotency-Key` → **201** → `SUCCEEDED` (1 attempt) → 1 segment,
26 words, `word` granularity, language `en`, RTF ≈ 0.25. All checks green
(health, providers, ordered timestamps, words available).

**UI half** (real browser, built app at `:4173`, live API at `:8200`):
- Upload page listed the provider and attached the real file
  (`sample.wav — 312.5 KB`), submit enabled.
- Submitting navigated to `/jobs/b227737f-…` → **SUCCEEDED** with metadata
  (faster-whisper, large-v3-turbo, en, 1 attempt, `wav — 0:10.0`).
- "View transcript" → transcript page rendered 1 segment, **26 word rows**
  with timestamps, the `<audio>` element (retained file), and the
  SRT/VTT/TXT download buttons; text: *"Hello, I'm Abdul Javid, AI Engineer
  at Home and Managed, …"*.
- Providers page: capability table rendered (health `ok`, 100 languages,
  word timestamps `yes`, formats list).

## TDD notes

Every frontend module landed through witnessed RED → GREEN: T1 (App shell),
T2 (client), T3 (upload), T4 (polling/job page), T5 (subtitles/viewer),
T6 (providers/evaluations/shell), plus the CORS test (backend, 2 failed
before the middleware). One real fix surfaced by the browser run:
`Faster-Whisper (local) (local)` — the backend provider name already
contains "(local)"; the suffix is now added only when absent.

## Limitations (honest)

- **No server-side job list** exists in `/v1`; the app keeps a best-effort
  localStorage index (cap 20). A list endpoint is a later-phase decision.
- **Playback is tab-local**: the uploaded file is retained in memory only;
  after a reload the transcript still renders and playback is honestly
  marked unavailable (the API serves no audio stream yet).
- **Evaluations** render a Phase-6 notice on 404 — no invented data.
- Polling only (spec); no SSE/WebSockets yet.
- MSW's node interceptor degrades multipart file parts to strings under
  vitest/jsdom, so the unit tests assert field serialization but not
  filename/bytes fidelity — that is proven by the live e2e instead.

## Next phase

Phase 4 per `IDEA.md`: additional providers behind the same contract
(Sarvam, Deepgram, ElevenLabs Scribe, Amazon Transcribe) with capability
discovery, plus the GPU/managed concurrency split the registry already
anticipates.
