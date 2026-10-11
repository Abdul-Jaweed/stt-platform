# Phase 6 — Evaluation (MLflow, reference datasets, WER/CER, comparison)

Spec: `IDEA.md` §"MLflow and evaluation" — versioned dataset with stable
audio IDs and human-verified reference transcripts; measure WER/CER,
timestamp availability and boundary error, queue wait / processing /
end-to-end / real-time factor, throughput and concurrency, CPU/GPU/memory,
failure and retry rates, cost per audio minute where calculable, accuracy
by language/accent/noise/domain; record model/provider version, decoding
settings, normalization, dataset version and configuration; "do not rank
providers on one recording"; add regression checks for material changes in
accuracy, timestamp quality, reliability and cost. Phase 6 scope:
"Add MLflow, reference datasets, WER/CER, timestamp evaluation, and
comparison reports." Endpoint list includes `GET /v1/evaluations/{evaluation_id}`.
Storage: PostgreSQL for evaluation metadata, RustFS for evaluation
artifacts. Development constraints: no audio, weights, credentials or
caches in git; artifacts stay on the I: drive.

## Design decisions

- **The dataset is a versioned manifest, not a pile of audio.** A committed
  JSON manifest carries stable clip ids, reference transcripts,
  language/domain/accent/noise metadata, reference segment boundaries and
  an explicit `reference_source`. Audio is referenced by path, lives on the
  I: drive, and is never committed (ADR-007). A committed generator script
  recreates it byte-for-byte on demand.
- **Two kinds of reference, never mixed up.** `synthetic-tts` clips are
  ground truth *by construction* (the text was spoken by the TTS engine, so
  it is known exactly) and are the default scored set — this is what makes
  WER numbers meaningful and CI-testable without a human in the loop. A
  real-speech clip (the owner's `audio/sample.wav`) is wired into the
  manifest as `pending-human-verification` and is **excluded from scored
  runs** until a human confirms its reference text. We do not invent an
  accuracy number for audio nobody has verified.
- **WER/CER live in this repo**, with the normalization pipeline written
  down and recorded in every evaluation result (lowercase, NFKC, strip
  punctuation, collapse whitespace; no number expansion). Tests cross-check
  the implementation against `jiwer` (dev-only dependency) so correctness
  is not self-certified.
- **Timestamp evaluation is explicit about what is measurable**:
  availability (do segments/words carry timestamps at all), boundary error
  against reference boundaries where they exist, coverage of the audio
  timeline, and monotonicity violations. Synthetic clips know their own
  boundaries (one clip = one sentence, 0 → duration), so boundary error is
  real, not decorative.
- **Operational metrics come from the job records**, not from a second
  stopwatch: queue wait, processing duration, end-to-end, real-time factor,
  attempts, retries, failures per clip — the same numbers Phase 5 exposes,
  so an evaluation and a dashboard never disagree.
- **MLflow logging is best-effort.** A dead tracking server must never fail
  an evaluation; the local record (PostgreSQL + RustFS artifact) is the
  source of truth and MLflow is the reproducible index over it. Client:
  `mlflow-skinny` (no server dependencies in the app).
- **Regression checks are part of the result**, not a human reading a
  table: `compare(baseline, candidate)` with explicit thresholds for
  accuracy, timestamp quality, reliability and cost, stored as a verdict.
- **One recording never ranks a provider** — a comparison requires at least
  two clips and reports the clip count next to every aggregate; a
  single-clip comparison is marked `insufficient_evidence`.

## MLflow server (already running)

The MLflow tracking server is the upstream docker-compose stack
(PostgreSQL + RustFS + MLflow 3.16.1), started once and kept outside this
repo:

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/mlflow/mlflow.git
cd mlflow && git sparse-checkout set docker-compose
cd docker-compose && cp .env.dev.example .env && docker compose up -d
```

UI and API: <http://127.0.0.1:5000> (health `/health`, UI `/`). The backend
connects through `STT_MLFLOW_TRACKING_URI` (default
`http://127.0.0.1:5000`); when it is unreachable the evaluation still
completes and the result says `mlflow: skipped (unreachable)`.

## Task 1 — Dataset manifest + generator

**Files:** `backend/src/stt_platform/evaluation/__init__.py`,
`evaluation/datasets.py` (manifest schema, load, validate, clip selection),
`evaluation/datasets/stt-eval-core.json` (versioned manifest),
`scripts/make_eval_corpus.py` (Windows SAPI TTS → 16 kHz mono WAVs + writes
the audio root), `tests/unit/test_eval_datasets.py`.

**Steps:** RED for: manifest load/validate, duplicate clip id rejected,
missing audio file reported not raised, `pending-human-verification` clips
excluded from scored selection, `scored_clips()` ordering stable.
Then implement; generate the corpus; assert the generator is idempotent.

## Task 2 — WER/CER + normalization

**Files:** `evaluation/metrics.py` (normalization, Levenshtein with
S/D/I breakdown, `wer`, `cer`), `tests/unit/test_eval_metrics.py`
(including a `jiwer` cross-check over generated pairs).

**Steps:** RED for normalization rules, WER on hand-computed cases
(substitution/insertion/deletion, empty reference, empty hypothesis),
CER, `jiwer` agreement.

## Task 3 — Timestamp evaluation

**Files:** `evaluation/timestamps.py`, `tests/unit/test_eval_timestamps.py`.

**Steps:** RED for availability, boundary error (mean absolute difference
of matched boundaries), coverage, monotonicity violations, and the
"provider returns no timestamps" path (availability 0, boundary error
`None`, never 0.0).

## Task 4 — Runner + aggregate result

**Files:** `evaluation/runner.py` (dataset × provider × decoding settings →
per-clip results + aggregate), `tests/integration/test_eval_runner.py`
(fake provider, deterministic transcripts), plus a `-m gpu` test that runs
the real corpus through faster-whisper.

**Steps:** RED for: per-clip record (transcript, metrics, timings,
attempts/retries), aggregate (micro and macro WER, per-language and
per-domain breakdown, RTF), failure isolation (one clip failing does not
abort the run, it is recorded as a failure and counted), and the
`insufficient_evidence` rule.

## Task 5 — Persistence + MLflow logging

**Files:** migration `…_evaluations.py` + `persistence/evaluation_models.py`
/`evaluation_repository.py`, `evaluation/mlflow_logging.py`,
`tests/integration/test_eval_persistence.py`,
`tests/integration/test_eval_mlflow.py` (skips when the tracking server is
unreachable), `tests/unit/test_eval_mlflow_payload.py` (fake client).

**Steps:** RED for: round-trip of an evaluation record, clip rows,
artifact upload to RustFS, MLflow run created with the documented
params/metrics/artifacts, and the unreachable-server path.

## Task 6 — API endpoints

**Files:** `api/v1/evaluations.py`, `schemas/evaluations.py`, router wiring,
`tests/integration/test_eval_api.py`.

**Steps:** RED for `POST /v1/evaluations` (bounded corpus; 201 with the
completed record; 404 unknown dataset; 422 clip cap exceeded),
`GET /v1/evaluations/{id}` (404 unknown), `GET /v1/evaluations` (list with
per-provider comparison and the clip-count honesty rule).

## Task 7 — Frontend: list, run, compare

**Files:** `frontend/src/pages/EvaluationsPage.tsx` (+ test), `api/client.ts`,
`api/types.ts`, styles.

**Steps:** RED for: list rendering, run form (dataset + provider), comparison
table (WER/CER/RTF/availability/failures, clip counts), the honest
`insufficient_evidence` and `mlflow: skipped` states, error states.

## Task 8 — e2e, docs, report, gates

**Files:** `scripts/e2e_phase6.py` (generate corpus → evaluate two
providers → assert metrics → verify the MLflow run through the tracking
API → `results/phase6_e2e/result.json`), `docs/evaluation.md`,
`docs/phase-6-report.md`, README/plan updates.

**Steps:** full gates (`uv run pytest -q`, `-m gpu`, `ruff`, frontend
`npm test` + `build`) → commit `docs(phase6): evaluation report and
metric catalog (T8)`.

## Acceptance criteria

- A versioned dataset exists with stable clip ids, reference transcripts,
  metadata and boundaries; unverified references are excluded from scoring
  and that exclusion is visible in the result.
- WER/CER are computed in-repo, normalized by documented rules, and
  agree with `jiwer` on generated cases.
- Timestamp availability and boundary error are measured, with "not
  reported by the provider" distinguished from "zero error".
- Every evaluation records provider/model version, decoding settings,
  normalization, dataset version and configuration.
- MLflow holds a run per evaluation with params, metrics and artifacts;
  an unreachable MLflow does not fail the evaluation.
- Comparison never ranks providers on one recording.
- Regression verdicts for accuracy, timestamp quality, reliability and
  cost are computed and stored.
- `/v1/evaluations` endpoints are live, tested, and consumed by the
  frontend; everything is documented in `docs/evaluation.md`.
