# Phase 6 report — Evaluation (MLflow, reference datasets, WER/CER, comparison)

Status: **complete** (T1–T8). Every number below comes from a command that
ran on this machine on 2026-10-11; nothing is estimated.

## 1. What was built

| Task | Deliverable | Commit |
| --- | --- | --- |
| T1 | Versioned dataset manifest + corpus generator with honest provenance | `fd8a01c` |
| T2 | WER/CER with recorded normalization, cross-checked against `jiwer` | `ed11f0f` |
| T3 | Timestamp quality: availability, boundary error, coverage, monotonicity | `98b221e` |
| T4 | Runner + platform-path transcriber + real-corpus GPU scoring | `2f57a7f` |
| T5 | MLflow tracking + PostgreSQL/artifact persistence + migration | `ef70d94` |
| T6 | `/v1/evaluations` API (run, read back, compare) | `9b69a33` |
| T7 | Frontend evaluations page + docs | `9b69a33` |
| T8 | End-to-end driver + this report | `9b69a33` + the commit carrying this file |

Source: `backend/src/stt_platform/evaluation/{datasets.py,metrics.py,timestamps.py,runner.py,platform.py,mlflow_logging.py,datasets/stt-eval-core.json}`,
`backend/src/stt_platform/persistence/evaluations.py` (+ the two tables in
`persistence/models.py`), `backend/src/stt_platform/schemas/evaluations.py`,
`backend/src/stt_platform/api/v1/evaluations.py`,
`backend/migrations/versions/91f43006a6bf_evaluations_and_evaluation_clips.py`,
`frontend/src/pages/EvaluationsPage.tsx`, `scripts/make_eval_corpus.py`,
`scripts/e2e_phase6.py`.

Tests: `tests/unit/{test_eval_datasets,test_eval_metrics,test_eval_timestamps,test_eval_runner,test_eval_mlflow_payload}.py`,
`tests/integration/{test_eval_runner_platform,test_eval_persistence,test_eval_mlflow,test_eval_api}.py`,
`tests/gpu/test_eval_gpu.py`, `frontend/src/pages/EvaluationsPage.test.tsx`.

## 2. Dependencies added

| Package | Scope | Why |
| --- | --- | --- |
| `mlflow-skinny` 3.17.0 | runtime | MLflow client (no server deps) for run logging |
| `jiwer` | dev only | cross-check our WER/CER in tests; never used to produce a recorded number |

## 3. MLflow server (docker compose)

Started exactly as documented upstream, from the sparse checkout:

```bash
cd /i/voice-agent-course
git clone --depth 1 --filter=blob:none --sparse https://github.com/mlflow/mlflow.git
cd mlflow && git sparse-checkout set docker-compose && cd docker-compose
cp .env.dev.example .env
docker compose up -d
```

Observed: `mlflow-server`, `postgres`, `storage` (RustFS) containers up;
`GET /health` → **200**, `GET /version` → **3.16.1**, UI → **200** on
`http://127.0.0.1:5000`. Host ports: MLflow 5000, its Postgres 5432, its
RustFS 9000/9001 — no clash with the project stack (25432/29000/7233/28080).

## 4. Commands and their real results

| Command | Result |
| --- | --- |
| `uv run python ../scripts/make_eval_corpus.py` | 9 clips generated (8 scored + 1 pending), ~59 s of audio, manifest filled with measured durations + sha256 |
| `uv run pytest -q` | **361 passed, 7 deselected** (was 271 at Phase-5 close; +90) |
| `uv run pytest -m gpu -q` | **3 passed** (2 existing + the real-corpus evaluation) |
| `uv run pytest tests/integration/test_eval_mlflow.py` | **3 passed** against the live tracking server |
| `uv run ruff check src tests` | clean |
| `npm test` (frontend) | **48 passed** (12 files; was 44) |
| `npm run build` | built, 282.40 kB js (88.89 kB gzip) |
| `uv run python ../scripts/e2e_phase6.py` | see §5 |

## 5. End-to-end evidence (`results/phase6_e2e/result.json`)

Real API process, real `large-v3-turbo` on CUDA, real tracking server, no
fakes: `POST /v1/evaluations` → **201** in 9.99 s, **8/8 clips scored, 0
failures, WER micro 0.100 (macro 0.118), CER 0.0946, RTF 0.107**,
evaluation id `b637d9b59f674f19b5e2b789909813b0`, artifact
`artifacts/evaluation/b637d9b59f674f19b5e2b789909813b0/result.json`.

The MLflow run (`d2b0bafab48644ff9526329fdef5c089`, FINISHED) was read back
from the tracking server: params `dataset_id=stt-eval-core`,
`dataset_version=1.0.0`, `provider=faster-whisper`, `model=large-v3-turbo`,
`clips_total=8`, `normalization=stt-normalize-v1`, `decoding={"language": "en"}`;
metrics equal to the API record (WER 0.1, CER 0.0946, RTF 0.107,
clips_scored 8.0); tag `insufficient_evidence=false`; and the artifact held
**all 8 clips with their transcripts**. `GET /v1/evaluations/{id}` returned
exactly the created record (`200`), the comparison list (`200`, 3 rows from
repeated runs) contained it with the same WER, and an unknown dataset
produced `404 DATASET_NOT_FOUND`. Per clip: 6 of 8 scored 0.000, dates
0.250, numbers 0.692 (see §6).

## 6. Measured baseline (GPU test, `results/phase6_gpu_eval.json`)

`large-v3-turbo` (CUDA), 8 scored clips, 59 s of audio:

| Metric | Value |
| --- | --- |
| WER micro / macro | 0.100 / 0.118 |
| CER micro / macro | 0.095 / 0.113 |
| RTF mean | 0.072 |
| Segment / word availability | 1.0 / 1.0 |
| Coverage | 0.825 |
| Boundary error mean / max | 0.512 s / 1.38 s |
| Monotonicity violations / degenerate spans | 0 / 0 |
| Processing p50 / p95 | 1.10 s / 1.30 s |
| Failures | 0 / 8 |

By domain: conversational 0.000, technical 0.000, dates-and-names 0.250,
**numbers 0.690**. By noise: clean 0.136, noisy 0.000.

The `numbers` score is a *normalization* consequence, not a model failure:
the reference is spelled out ("three hundred and forty two dollars") and
Whisper writes digits ("342 dollars"). The rules are recorded with every
result and documented in `docs/evaluation.md` §3 — expanding numbers was
rejected because it would hide a real difference between what was said and
what was written.

## 7. Defects found and fixed during the phase

1. **`mlflow` client `download_artifacts()` is unusable against this compose
   setup** — it retried 8 chunk downloads against the server-proxied S3 store
   and took minutes (the first test run had to be killed at 420 s). Artifacts
   *are* stored and readable: the tracking server's
   `/get-artifact?path=...&run_uuid=...` endpoint returns them immediately.
   The tests and the e2e use that endpoint; the limitation is documented.
2. **The proxied artifact path uses the experiment id, not the run id**
   (`/api/2.0/mlflow-artifacts/artifacts/<experiment_id>/<run_id>/artifacts/...`)
   — a 404 from guessing the wrong prefix sent me to the S3 bucket listing to
   confirm the objects were really there.
3. **MLflow's REST `runs/get` returns params/metrics as `[{"key","value"}]`
   lists**, not dicts — the first e2e run failed on `'list' object has no
   attribute 'get'` after the evaluation itself had already succeeded.
4. **`record.model` is not what the provider reported** — the job record only
   carries a request hint, so the evaluation named no model until the
   platform transcriber preferred the transcript's own `model` field.
5. **Manifest-relative vs repo-relative audio paths** — the loader resolves
   `audio_root` against the repository root; the first unit tests pointed at
   tmp_path and resolved the wrong directory. Resolution is now explicit
   (settings → env → manifest) and the tests pass the root they mean.
6. **A test fake that only implemented `put_bytes` failed every job with
   `STORAGE_ERROR`** — the sync path also calls `put_file`; the in-memory
   store in the API tests now implements the four methods the service uses.
7. **The API path logged a run with no artifact** — `log_evaluation` only
   uploaded `result.json` when a caller-supplied directory existed, so runs
   created through `POST /v1/evaluations` had no transcript-bearing artifact
   (the tracking server's `/get-artifact` answered 500). Logging now always
   writes and uploads the result, using a temp directory when the caller has
   none.
8. **`pytest`'s tmp-path fixtures made the corpus a CI dependency** — the API
   tests now point `evaluation_datasets_dir`/`evaluation_audio_root` at a
   two-clip tmp corpus, so the suite is hermetic.

## 8. Limitations (stated, not hidden)

- The shipped corpus is **synthetic and English-only** (the machine's SAPI
  voices are English). Real speech is wired but its reference transcript is
  `pending-human-verification`, so it is **excluded from scoring** until a
  human confirms it — the owner's `audio/sample.wav` is in exactly that
  state.
- Reference boundaries are clip-level (0 → duration), so `boundary_error_s`
  includes leading/trailing silence and is a coarse signal.
- Provider comparison in the e2e uses the local provider only: the managed
  providers are key-gated and comparing them would spend the owner's
  free-tier quota. Cross-provider comparison *machinery* is proven with fakes
  (integration tests) and in MLflow (`test_two_providers_are_comparable_in_mlflow`).
- `POST /v1/evaluations` is synchronous with a cap
  (`STT_EVALUATION_MAX_CLIPS`, default 25); a larger corpus needs a workflow,
  which is out of scope for this phase.
- Cost per audio minute is **not** computed (no reliable price source);
  the inputs are recorded so it can be added later without re-running.
- MLflow is best-effort: if the tracking server is down the evaluation still
  succeeds and its `mlflow` field says `error` with the reason.

## 9. Next phase

**Phase 7 — Hardening**: end-to-end, load, failure-recovery, security,
tenant-isolation, retention and deletion tests, plus operations and incident
recovery documentation.
