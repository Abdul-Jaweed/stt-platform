# Master plan — Evidance revamp (production readiness, observability, evaluations, docs, deployment)

Status: **in progress** · Directive: "Master Engineering Task … for Evidance"
(pasted 2026-10-12) · Repo: `Abdul-Jaweed/stt-platform` (public)

## Naming decision (flagged for correction)

The directive calls the product **Evidance**; no `Evidance` repo or directory
exists, and every specific it lists — four providers with ElevenLabs deferred,
WER/CER evaluation, archify diagrams, the one-command Docker stack, demo
credentials on the login page — maps 1:1 onto this repository. Therefore:

* **Evidance** becomes the product name in user-facing surfaces (landing page,
  console title, README, docs site).
* The **repo slug** (`stt-platform`), the **Python package** (`stt_platform`),
  the image names and the compose service names stay as they are: the directive
  asks for a product, not a repo migration, and renaming the package would
  break every existing deployment for no requirement. Reversal is a
  find-and-replace in `frontend/src` + `docs/`.

## Audit — what exists, what the directive asks for

| Directive | Current state | Work |
| --- | --- | --- |
| §2 repo-wide audit | phases 0–8 shipped, tree clean at `7701cff`, 9 phase reports | this document |
| §3 landing page | `LandingPage.tsx` exists (hero, features, providers from live API) | revamp: workflow/architecture section, richer capabilities from `/v1/providers`, states, a11y, Evidance identity |
| §4 evaluation page | `EvaluationsPage.tsx` shows runs + aggregate WER/CER; per-clip detail exists in the artifact but is not surfaced; no metric explanations; no reference-vs-generated diff | **largest frontend slice**: overview, metric explanations, diff view, per-sample table, methodology |
| §5 real-time for every provider | local streaming shipped (Phase 8, `streaming: true` verified); managed adapters `streaming: false` | shared real-time contract + adapters for the vendors whose API supports it (Deepgram, Sarvam, ElevenLabs realtime), capability-gated, live-verified in bounded runs |
| §6 segment timestamps | canonical `TranscriptSegment`/`TranscriptWord`, seconds-float, loud error on degenerate, batch+stream share the model | verify/patch gaps: provider raw metadata preservation, invalid-interval handling documented, tests for conversion/precision |
| §7 max-duration info | `ProviderCapabilities` **already has** `max_duration_s`, `max_file_bytes`, `formats` — populated for some providers, not all; UI shows capabilities but not limits-vs-request-validation alignment | state authoritative values per provider/model with provenance, align with `max_sync_duration_s`/upload caps, surface on `/providers`, test consistency |
| §8 demo credentials | login page shows a server-driven dev-credentials hint (`AuthConfig`) | add one-click fill, demo-mode gating (`STT_AUTH_DEMO`), security tests + docs |
| §9 OTel by default | `otel_enabled: bool = False` (Phase 5 deliberately opt-in) — the directive reverses that default | flip to on for dev/compose, keep `STT_OTEL_ENABLED=false` as the off switch, prove non-blocking + no duplicate spans, document |
| §10 docs overhaul | `docs/`: 9 phase reports, api/ui/deployment/observability/evaluation/providers/operations/incident-recovery, archify HTML | README OSS rewrite, architecture update, the §12 technical guide, troubleshooting, aligned with code |
| §11 GitHub Pages | no `.github/`, no site tooling; docs are plain markdown + two archify HTML artifacts | MkDocs (Material) site: nav over `docs/`, archify artifacts linked, GH Actions build+deploy, local `mkdocs build --strict` gate, **publication status reported honestly** |
| §12 pipeline/reliability guide | knowledge exists across phase reports + `docs/architecture/` | one dedicated guide answering §12.1–12.7 with implementation references, example payloads, and explicit *proposed* labels for gaps |
| §13 Docker stack | `launch.sh` one-command stack, healthchecks, `.dockerignore`, pinned images, GPU opt-in, `clean` reset | audit: non-root, restart policies, env consistency, fresh-environment verification, documented prerequisites |
| §14 ElevenLabs deferred | adapter built; live-verified **with a real key** on 2026-10-11/12 (evidence in `docs/live-provider-verification.md`); registers only while its key is present; `streaming: false` | keep the honest verified claims; add its real-time adapter only if the vendor API supports it; never claim more than a live run showed |
| §15 tests | 524 backend + 175 frontend + 8 e2e drivers + `-m gpu` + `-m live_provider` | add: provider-streaming mocks, OpenAPI/route completeness, metrics-explanation consistency, capability-limit consistency, docs build in CI |
| §16–20 principles/report | — | TDD everywhere, gates green per commit, final report with passed/failed/blocked/not-run |

## Standing rules (unchanged from phases 0–8)

1. **TDD**: witnessed RED before implementation for every production change.
2. Gates per commit: `uv run pytest -q` + `uv run ruff check src tests ../scripts`
   (from `backend/`), `npm test` + `npm run build` (from `frontend/`), e2e
   drivers for behaviour that only the live stack can prove.
3. Honest reporting: a metric that could not be measured is `None`, a check that
   could not run is "not run" with the reason, a capability is `verified` only
   after a live run backed it.
4. No secrets in git/frontend/logs; no paid call without a stated reason; one
   bounded live verification per vendor event.
5. Small commits, one slice each, pushed to `main` as they land.

## Slices

**S1 — capability + limit truth pass** (backend, tests)
Authoritative `max_duration_s` / `max_file_bytes` / `formats` per provider and
model, each with provenance (documented vs verified), aligned with the enforced
request validation; unknown values stay `None` (never invented). Surface in
`/v1/providers`. Acceptance: every value either traces to vendor docs (recorded
in `docs/providers.md`) or is `None`; a test pins each provider's set and the
alignment with `Settings` limits.

**S2 — OpenTelemetry on by default** (backend, compose, tests, docs)
`otel_enabled` default true for dev + compose; `STT_OTEL_ENABLED=false` is the
documented off switch; exporters configurable; a dead collector must never fail
a transcription (existing contract); no duplicate spans. Acceptance: unit tests
for the default + off switch; live trace of one upload→provider→persist
workflow (console exporter evidence); docs updated.

**S3 — OpenAPI + route completeness** (backend, tests)
Export `openapi.json` (committed), a test asserting every implemented route and
WS endpoint appears in the spec and that examples in `docs/api.md` reference
real routes; document `/openapi.json` + `/docs` availability.
Acceptance: test fails on an undocumented route.

**S4 — demo login, safely** (backend + frontend, tests, docs)
`AuthConfig` gains `demo_mode`; the login page can prefill demo credentials with
one click; demo mode is off by default, documented, and cannot bypass real auth
when `STT_AUTH_ENABLED=true` without the explicit flag. Acceptance: tests for
both modes; docs section "Demo access".

**S5 — evaluation explainability, backend side** (backend, tests)
The metrics module publishes its own explanation metadata (formula, direction,
normalization, inputs, edge behaviour, limitations) for every metric it
implements; the evaluation artifact carries per-clip detail (reference,
hypothesis, counts, per-clip metrics) and a token-level alignment (S/D/I ops)
produced by the same machinery that computes WER. Acceptance: explanations
generated from the implementation, not a duplicated string table; alignment
reproduces the reported counts exactly (property test).

**S6 — evaluations UX revamp** (frontend, tests)
Overview table (id, provider/model, dataset, scored/skipped/pending, status,
timestamps, aggregates), run detail (metric cards + explanations, reference vs
generated side-by-side with highlighted substitutions/deletions/insertions,
per-clip table with search/sort/pagination, methodology panel with versions and
settings), and a clear distinction between a transcription result and an
aggregate run. Acceptance: component tests + a live-stack screenshot check.

**S7 — landing page revamp** (frontend, tests)
Evidance identity, hero + CTAs (app, docs, repo), capabilities straight from
`/v1/providers` (no invented claims), workflow/architecture overview, honest
limits, states for unavailable API, a11y (landmarks, contrast, focus).

**S8 — real-time across providers** (backend + frontend, tests, live)
Shared session contract for managed streaming; adapters for vendors whose
public API supports real-time (verify each vendor's docs first — Deepgram,
Sarvam, ElevenLabs); interim/final semantics, ordering, cancellation, error
mapping, dedupe; capability flags flipped only per live run; console can pick
any streaming-capable provider. Acceptance: mocked per-vendor tests +
`-m live_provider` bounded runs + capability provenance updated.

**S9 — documentation overhaul** (docs)
README (OSS quality, verified commands only), architecture update, the §12
technical guide (`docs/pipeline-and-reliability.md`), API docs refresh,
troubleshooting, env reference. Acceptance: every command in the docs executed
at least once; links checked in the site build.

**S10 — GitHub Pages** (CI + docs)
MkDocs Material site over `docs/` (archify HTML linked, not duplicated), GitHub
Actions workflow (strict build + deploy), local preview documented, Pages
enabled via `gh api` if the token allows; publication reported as verified or
as the exact external step remaining.

**S11 — Docker audit + fresh-environment proof** (compose, scripts, docs)
Non-root where practical, restart policies, healthcheck/dependency audit,
`.env.example` completeness, `launch.sh clean` reset verified from a clean
state, documented prerequisites; GPU path re-verified.

**S12 — final verification + report** (§15, §20)
All gates, all e2e drivers, docs build, fresh Docker run; the final engineering
report with A–G sections and an honest passed/failed/blocked/not-run table.

## Deliverables log

(Appended as slices land — commit per slice.)

### S1 — capability + limit truth pass ✅

- `audio/limits.py::check_provider_limits` — the single enforcement primitive
  (duration → `AudioTooLong`, size → `UploadTooLarge`, message names the
  provider and quotes its number).
- Wired into all four entry points: the API pre-flight (route resolves the
  provider's limits through the new `JobService.provider_limits`), the
  asynchronous create path, the synchronous `_process`, and the worker's
  `transcribe` activity (so a presigned upload that arrives with no pre-flight
  is still checked before any vendor call).
- Declared limits, each `documented` with the vendor page recorded in
  `docs/providers.md`: Deepgram 2 GB, ElevenLabs 3 GB / 10 h, Sarvam 2 h
  (batch). faster-whisper and every undocumented value stay `None` — the
  platform refuses nothing on a vendor's behalf without a number.
- `GET /v1/providers` now also returns `limits` — the deployment's own caps —
  so the console can explain a refusal without re-typing numbers.
- Providers page: **Max file** column, explicit "no limit documented" instead
  of a dash, and the deployment's caps stated next to the provider table.
- Tests: `test_provider_limits.py` (helper), an API pre-flight test, a
  service-level test, provenance pins for every populated number, the
  endpoint's `limits` block, `client.getProviderDirectory`, and the table's
  new cells.
- Also corrected: `docs/providers.md`'s matrix still said ElevenLabs
  diarization was only *documented*; the 2026-10-12 live run had already
  moved it to *verified* in code — docs now match.
