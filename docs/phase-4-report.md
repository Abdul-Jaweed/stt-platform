# Phase 4 Report — Managed Providers

**Status:** complete · **Date:** 2026-10-11 · **Plan:**
`docs/superpowers/plans/2026-10-11-phase-4-managed-providers.md` (all
tasks ticked)

## Objective

Add Sarvam, Deepgram, and ElevenLabs Scribe behind the same internal
provider contract, with asynchronous-provider support in the workflow
(submit → poll → fetch), a per-class concurrency split, and honest
capability reporting (`documented` vs `verified`). Amazon Transcribe stays
deferred until AWS setup is verified (spec §"Initial providers").

## Deliverables

| Task | Deliverable | Commit |
|---|---|---|
| T1 | Async provider contract (`RemoteJobRef`/`RemoteJobState`/`AsyncProviderProtocol`), workflow polling loop, `provider_attempts` table + repository, forced-async routing, `remote` workflow payload | `327eb8a` |
| T2 | `ManagedHttpClient` (auth, timeouts, error mapping, size guard, key redaction), key-gated `build_default_registry`, GPU/managed semaphores, activity-slot split | `4c71223` |
| T3 | Deepgram adapter (nova-3, utterances, diarization, punctuated words) | `b5da965` |
| T4 | ElevenLabs Scribe adapter (multipart, word runs, no fabricated confidence) | `4479e10` |
| T5 | Sarvam batch adapter (async submit/poll/fetch, diarized entries) + live-Temporal integration test | `d03cf52` |
| T6 | Live-provider scaffolding (`-m live_provider`), fake-server e2e driver | `f099563` |
| T7 | This report, `docs/providers.md`, README, `.env.example`, plan ticked | this commit |

## Dependencies added

Runtime: `httpx`. Dev: `respx` (httpx mocking). `uv.lock` updated. No
frontend changes.

## Commands executed (real output)

```
backend:  uv run pytest -q                → 207 passed, 5 deselected
backend:  uv run pytest -m gpu -q         → 2 passed, 210 deselected
backend:  uv run ruff check src tests     → All checks passed!
frontend: npm test                        → 12 files, 44 passed
frontend: npm run build                   → ✓ built (87.62 kB gzip)
e2e:      uv run python ../scripts/e2e_phase4.py → passed: true
```

## Live e2e evidence (`results/phase4_e2e/result.json`)

The driver starts an **in-process fake Sarvam server** (documented batch
flow), the API (`:8210`) and the Temporal worker with
`STT_SARVAM_API_KEY` + `STT_SARVAM_BASE_URL` pointed at the fake, then:

- `/v1/providers` lists exactly `faster-whisper` + `sarvam` (key-gated);
- `POST /v1/transcriptions` with `provider=sarvam` on `audio/sample.wav`
  → **202** (async-capable provider forced onto the workflow path);
- job → **SUCCEEDED**; canonical result: provider `sarvam`, model
  `saaras:v4`, `timestamp_granularity: segment`, 2 segments with
  speakers `[0, 1]`, language `en-IN`, no warnings;
- `provider_attempts`: **exactly one row** — attempt 1, `COMPLETED`,
  `provider_job_id = job-e2e-1`;
- the fake server saw the complete documented flow:
  initiate → upload-files → presigned PUT → start → status ×2 →
  download-files → presigned GET.

No paid API calls were made anywhere in this phase's tests or e2e.

## TDD notes

Every task landed through witnessed RED → GREEN (unit fixtures under
respx; live-Temporal integration tests for the async path). Two real
defects were caught by the tests rather than the tests being bent:

1. **Cancellation during a durable sleep** raised `asyncio.CancelledError`
   while a cancel delivered at an activity await raises the SDK's
   `CancelledError`; the handler matched only the latter, so a job
   cancelled mid-poll kept `PROCESSING`. The except clause now covers both
   (with a regression test asserting `provider.cancels == 1`).
2. **The e2e driver hit a real deployment gap**: the compose database had
   no `provider_attempts` table because migrations were never applied by
   the driver. The script now runs `alembic upgrade head` before starting
   the stack — the same step a deployer must run.

## Deviations from the plan (all deliberate)

- **Scribe model**: default is `scribe_v2` (current documented SOTA at
  implementation time), not the plan's `scribe_v1`; `scribe_v1` remains
  selectable via `STT_ELEVENLABS_MODEL_ID`.
- **Sarvam is a multi-step batch API** (initiate → upload-files → start →
  status → download-files), not the single submit endpoint the plan
  assumed. The adapter implements the documented flow, including
  credential-free presigned transfers.
- **`ProviderTranscript.warnings`** was added so dropped degenerate spans
  are visible in the canonical result (`TranscriptResult.warnings`),
  satisfying "never silently clamp".
- **Registry wiring** landed in T2 (builder + no-key behaviour); the
  per-provider key-set assertions were added with each adapter.
- **Ruff rule set pinned** (T7 close-out): the T2 `uv add` bumped ruff to
  0.16, whose widened *default* rule set surfaced 43 findings that the
  T1-era default (E4/E7/E9/F) never checked. `backend/pyproject.toml` now
  pins `line-length = 100` and an explicit `select`, with `B008`
  (FastAPI's DI idiom) and `UP042` (keeping the `(str, Enum)` mixin for
  `JobStatus`) ignored *with reasons*. The genuine findings were fixed:
  nested-`if` merges, `datetime.UTC`, `itertools.pairwise`, an explicit
  `check=False` on the ffprobe probe, a loop-variable shadow, and a blind
  `pytest.raises(Exception)` narrowed to `IntegrityError`.

## Limitations (honest)

- **No managed provider is `verified` yet**: no real API keys were
  available, so every managed capability remains `documented`. The
  key-gated live suite (`uv run pytest -m live_provider`) is the one
  sanctioned way to flip that, and the gating itself is pinned by tests
  (deselected in default gates, collectible only when asked for).

  *Superseded 2026-10-11:* free-tier keys were supplied and all three
  managed providers were live-verified (Deepgram, ElevenLabs, Sarvam — one
  10-second transcription each, STT endpoints only). Five real defects were
  found and fixed under TDD; the provenance flips are pinned by
  `tests/unit/test_capability_provenance.py`. Full record:
  `docs/live-provider-verification.md`.
- **Amazon Transcribe is deferred** (no verified AWS setup), as the plan
  states.
- **Sarvam cancel is unsupported** — the vendor documents no cancel
  endpoint for batch jobs; `cancel` reports `False` and the job still
  records `CANCELLED` locally.
- **Sarvam `PartiallyCompleted`** is treated as completed (outputs are
  fetched for successful files); per-file failure accounting is not yet
  surfaced beyond the warnings list.
- The e2e uses a **fake** Sarvam server: it proves our side of the
  contract (request shapes, polling, persistence, canonicalization), not
  vendor behaviour under real conditions.
- Presigned uploads assume Azure-style block-blob PUT semantics
  (`x-ms-blob-type: BlockBlob`, harmless on other backends) — verified
  against the fake, not against Sarvam's production storage.
- Deepgram/ElevenLabs language lists are conservative subsets of what the
  vendors document.

## Next phase

Phase 5 per `IDEA.md` (evaluation: human-verified references, WER/MER
scoring, provider comparison harness) on top of the now-swappable
provider layer.
