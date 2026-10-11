# Phase 5 report — Observability

**Status: complete.** Traces, metrics, structured logs, a dashboard and
alert rules, all off by default and none of them able to break a
transcription. Operational documentation lives in `docs/observability.md`;
this file records what changed, how it was proven, and what is honestly
missing.

Spec: `IDEA.md` §"Observability". Plan:
`docs/superpowers/plans/2026-10-11-phase-5-observability.md`.

## Commits

| Task | Commit | What |
| --- | --- | --- |
| plan | `ef4535a` | Phase-5 plan |
| T1 | `7aeea14` | tracing provider, no-op mode, redaction |
| T2 | `fb14d99` | API spans, HTTP metrics, correlation ids |
| T3 | `03422f1`, `5f1072e` | Temporal interceptors, activity spans, provider metrics |
| T4 | `a5c4f7c` | structured JSON logs with trace/correlation/job ids |
| T5 | `3bed34f` | live trace propagation across the Temporal boundary |
| T6+T7 | `c0e5028` | runtime gauges, Prometheus endpoint, dashboard, alerts |
| T8 | `91f1654` + the commit carrying this file | e2e driver, stale-workflow fix, upload-size metric, docs, gates |

## What shipped

New package `backend/src/stt_platform/observability/`:

| File | Role |
| --- | --- |
| `tracing.py` | tracer provider (OTLP/console/none), `active_provider()`, context-aware span processor |
| `redaction.py` | key-shaped material → `***`, applied to records and span attributes |
| `names.py` | the 21 canonical metric names (single source of truth) |
| `context.py` | contextvars for correlation id and job id |
| `metrics.py` | instruments, record helpers, runtime snapshot |
| `instrumentation.py` | one hand-written ASGI middleware: correlation id, server span, HTTP metrics, access log |
| `temporal.py` | Temporal client/worker interceptors + `activity_span()` helper |
| `logging.py` | plain-text or one-redacted-JSON-object-per-line, with ids |
| `runtime.py` | CPU/RSS/GPU/queue-depth sampler on a daemon thread |
| `prometheus.py` | reader + registry, `render_metrics()`, worker-side HTTP endpoint |
| `dashboards/stt-overview.json` | Grafana dashboard, 20 panels |
| `alerts/stt-alerts.yaml` | 18 Prometheus alert rules |

Wired into `main.py` (API), `workers/transcription_worker.py` (worker),
`workers/activities.py` (transition/attempt/poll/cancel spans and job
metrics), `services/temporal_starter.py` (client interceptor),
`services/jobs.py` (upload size), `providers/*` (provider call metrics and
spans), `core/config.py` (`otel_*`, `log_json`, `log_level`,
`runtime_sample_interval_s`).

Dependencies added (all pinned in `pyproject.toml` + `uv.lock`):
`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`,
`opentelemetry-exporter-prometheus`, `opentelemetry-instrumentation-fastapi`,
`psutil`, `pynvml`.

## Evidence

### Tests (all executed, none skipped)

```bash
cd backend
uv run pytest -q                 # unit + integration, full suite
uv run pytest -m gpu -q          # GPU marker
uv run ruff check src tests
cd ../frontend && npm test && npm run build
```

Phase-5 test files:

| File | Covers |
| --- | --- |
| `tests/unit/test_observability_core.py` | provider/no-op mode, redaction, span parenting, ordering |
| `tests/unit/test_temporal_tracing.py` | interceptor wiring, `activity_span` parenting |
| `tests/unit/test_observability_logs.py` | JSON shape, ids, redaction through the handler |
| `tests/unit/test_runtime_sampler.py` | sampler thread, GPU-absent path, snapshot merge/reset |
| `tests/unit/test_prometheus_artifacts.py` | dashboard + alerts reference only registered metrics |
| `tests/integration/test_observability_api.py` | real request → span, HTTP metrics, route labels, access log, disabled mode |
| `tests/integration/test_activity_observability.py` | activity spans, provider outcome mapping, job metrics |
| `tests/integration/test_trace_propagation.py` | one trace id across API → workflow → activity (live Temporal) |
| `tests/integration/test_job_service_storage.py` | upload size recorded where the bytes are known |

### Real-stack e2e — `scripts/e2e_phase5.py`

Two real processes (uvicorn API + Temporal worker), one real job on the
10 s sample through the queued path, both `/metrics` endpoints scraped, and
the child logs parsed. Last run (2026-10-11), `results/phase5_e2e/`:

| Fact | Value |
| --- | --- |
| POST `/v1/transcriptions` | **202** |
| Status trail | `QUEUED → PROCESSING → SUCCEEDED` |
| Transcript | 26 words, 1 segment, `large-v3-turbo`, `en`, 10.0 s audio |
| Processing duration | 0.939 s (real-time factor **0.094**) |
| API metrics | `stt_api_requests_total` 1, duration count 1, `stt_upload_bytes_count` 1, process RSS, queue depth |
| Worker metrics | end-to-end (SUCCEEDED) 1, queue wait 1, processing 1, RTF count 1 (sum 0.0939), provider calls `ok` 1, provider duration 1 |
| Worker spans | workflow (`StartWorkflow`/`RunWorkflow`/`CompleteWorkflow`), activities (`StartActivity`/`RunActivity` ×2), domain (`stt.transition`, `stt.attempt`, `stt.provider.transcribe`) |
| Trace ids | the worker exported exactly **one** trace id — the API request's (`cce5d080…`) → `trace_propagated_api_to_worker: true` |
| Job id on spans | `job_id_on_spans: true` |
| Access log | 9 JSON lines, `correlation_id=e2e-phase5`, `trace_id=cce5d080…`, 285.14 ms |
| Redaction | no API key from `backend/.env` appears in either log |
| Leftovers | ports 8078/9466/9467 free after the run (the kill-tree fix works) |

### Gate results (final state)

| Gate | Result |
| --- | --- |
| `uv run pytest -q` | **271 passed, 6 deselected** (101.8 s) |
| `uv run pytest -m gpu -q` | **2 passed, 275 deselected** |
| `uv run ruff check src tests` | clean |
| `npm test` (frontend) | **44 passed** (12 files) |
| `npm run build` | built in 1.03 s |
| `scripts/e2e_phase5.py` | **passed: true** (final run above) |

## Defects found and fixed while proving it

1. **Stale processes made earlier runs lie.** `uv run` spawns python as a
   child; terminating the wrapper left the server alive, and on Windows two
   sockets can bind the same port without an error — so a scrape could hit
   a process from an earlier run. `kill_tree` now kills the whole tree and
   the driver refuses to start if its ports are busy. *This is why the
   first four e2e runs produced impossible readings.*
2. **The e2e was measuring the wrong path.** A 10 s file is transcribed
   inline in the API request (`max_sync_duration_s`, default 180 s), so no
   workflow, no worker, no job metrics. The driver now sets
   `STT_MAX_SYNC_DURATION_S=0` to force the queued path — and asserts 202.
3. **A cancelled job's workflow retried an illegal transition forever.**
   `CANCELLED → FAILED` is not a legal transition; the activity raised and
   Temporal retried it (attempt 6 observed). The transition activity now
   treats an illegal transition as a no-op (`written=False`) and logs a
   warning, because the database state is authoritative and a terminal job
   must never be re-transitioned. Regression test:
   `test_transition_from_a_terminal_state_is_a_noop`.
4. **Upload size was never measured.** `stt_upload_bytes` existed as an
   instrument but nothing recorded it; now both the sync and the async
   accept path record it, with a test asserting the observed size.

## Limitations (honest list)

* **Cost estimates are not implemented** — no reliable price source; the
  inputs (upload size, provider durations, provider identity) are recorded
  instead of an invented number.
* **Metrics are per process.** API metrics exist only in the API, job and
  provider metrics only in the worker; a scraper must hit both. Nothing
  aggregates them for you.
* **Short jobs are invisible to the workflow metrics** (see defect 2). That
  is existing product behaviour, not a Phase-5 gap, but it surprises people.
* The **console exporter** is for verification only; production needs an
  OTLP collector. Retention is the collector's business — this repo stores
  no telemetry.
* GPU gauges are absent (not zero) without an NVIDIA driver.
* FastAPI 0.133's own telemetry adds `fastapi.*` spans and `http_server_*`
  metrics when a provider is installed. They are ignored by our dashboard
  and alerts on purpose; the two sets are not merged.

## Acceptance criteria

| Criterion | Status |
| --- | --- |
| Instrumentation for API, workflows, activities, providers, storage | ✅ tests assert real spans/metrics |
| Trace context propagates API → workflow → activity | ✅ in-process test + real-process e2e |
| Spec's metric list covered (queue wait, end-to-end, provider latency/errors/throttling, processing, RTF, retries, fallbacks, queue depth, CPU/RSS/GPU, storage errors) | ✅ 21 names in `names.py`, all recorded from real code paths |
| Cost estimates "when reliable" | ⚠️ not implemented, documented as such |
| Structured logs with correlation + job ids; no secrets/audio/transcripts | ✅ redaction unit tests + e2e credential scan |
| Liveness ≠ readiness | ✅ unchanged, pinned by the Phase-1 test |
| Dashboard + alerts shipped and validated against the registry | ✅ 20 panels, 18 alerts, registry test |
| Off by default, cannot break a transcription | ✅ no-op providers by default; e2e runs a real job with it on |

## Next

Phase 6 — evaluation (MLflow): accuracy measurement against human-verified
references, per-provider comparison, and the evaluation endpoints the spec
lists. The Phase-4 live runs and this phase's metrics give it the raw
material: same 10 s sample, same providers, reproducible job ids.
