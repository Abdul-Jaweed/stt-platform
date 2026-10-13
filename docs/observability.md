# Observability

Phase 5 wired OpenTelemetry through the API, the workflow, the worker and
every provider call. Phase 9 (the master revamp) flipped the default: it
is **on** in the standard dev and Docker configurations, because
instrumentation that has to be discovered before it can help is
instrumentation nobody reads.

Three rules decide everything here:

1. **On by default, optional by contract.** `STT_OTEL_ENABLED=false`
   restores the no-op providers (a function call and nothing else). With
   tracing on and *no collector listening*, exports fail quietly in the
   background and transcription is untouched — `test_an_unreachable_
   collector_is_not_an_error` and the live verification below pin that.
2. **One source of truth for names.** Every metric name lives in
   `observability/names.py`; the instruments are built from it and the
   dashboard and alert rules are validated against it, so a name that
   exists in one place fails the test suite.
3. **Nothing sensitive is recorded.** Log records are redacted and spans
   carry ids, sizes and durations — never audio, never a transcript body,
   never a credential.

## Turning it on

Everything is env-gated (`backend/.env`, see `.env.example`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `STT_OTEL_ENABLED` | `true` | Master switch for traces and metrics (`false` = no-op providers) |
| `STT_OTEL_EXPORTER` | `otlp` | `otlp` \| `console` \| `none` (`console` prints spans — for verification only) |
| `STT_OTEL_ENDPOINT` | `http://127.0.0.1:4318` | OTLP/HTTP collector endpoint |
| `STT_OTEL_SERVICE_NAME` | `stt-platform` | `service.name` resource attribute |
| `STT_OTEL_ENVIRONMENT` | `local` | `deployment.environment` |
| `STT_OTEL_SAMPLE_RATIO` | `1.0` | Trace sampling ratio |
| `STT_LOG_JSON` | `false` | Plain text (default) or one redacted JSON object per line |
| `STT_LOG_LEVEL` | `INFO` | Root log level |
| `STT_PROMETHEUS_ENABLED` | `false` | Serve `/metrics` for scraping |
| `STT_PROMETHEUS_PORT` | `9464` | Port for that endpoint (API serves it on its own route too) |
| `STT_RUNTIME_SAMPLE_INTERVAL_S` | `15` | How often CPU/memory/GPU/queue gauges are refreshed |

With `STT_OTEL_ENABLED=false` the tracer and meter providers are never
created: the OpenTelemetry API's no-op implementations are used, so span
and metric calls cost a function call and nothing else.

### Reading traces locally

No collector is required. Two ways to see real spans from a real request:

```bash
# 1. Straight from the log (any deployment): one JSON span per line.
STT_OTEL_EXPORTER=console  # set in .env, then ./launch.sh restart
./launch.sh logs api | grep '"name"'

# 2. Any OTLP/HTTP endpoint — e.g. a throwaway Jaeger UI:
docker run -d --name jaeger -p 16686:16686 -p 4318:4318 jaegertracing/all-in-one
# then STT_OTEL_ENDPOINT=<an address the containers can reach> in .env
```

The compose file forwards `STT_OTEL_ENABLED`, `STT_OTEL_EXPORTER` and
`STT_OTEL_ENDPOINT` into both the API and the worker
(`backend/tests/unit/test_compose_env.py` holds that wiring still).

### Verified on the live stack (2026-10-12)

`scripts/observability_evidence.py` drove the running deployment through all
of this — 5/5 checks, raw logs in `results/observability/`:

| Check | Result |
| --- | --- |
| a real upload with `otlp` aimed at a dead endpoint | **SUCCEEDED** — tracing is never a failure mode |
| API spans for the synchronous path | `POST /v1/transcriptions`, `GET /v1/transcriptions/{job_id}`, `StartWorkflow:TranscriptionWorkflow`, … |
| worker spans for the asynchronous path | `RunActivity:execute_attempt`, `stt.attempt`, `stt.provider.transcribe`, … |
| trace context across the process boundary | one trace id present in **both** the API and the worker log (e.g. `23b87fbc…`), i.e. the workflow inherits the request's trace instead of starting an unrelated one |

Log records carry `trace_id`/`span_id` in their JSON form
(`STT_LOG_JSON=true`), which is what makes a span id quoted by an error
report findable in the logs.

## Traces

| Span | Created in | Attributes |
| --- | --- | --- |
| `POST /v1/transcriptions` (method + route) | API middleware | `http.route`, `http.request.method`, `http.response.status_code`, `http.request_id` |
| `RunWorkflow:TranscriptionWorkflow`, `StartActivity:*`, `RunActivity:*`, `CompleteWorkflow:*` | Temporal interceptor | Temporal's own naming |
| `stt.transition` | `workers/activities.py` | `stt.job_id`, `stt.scope`, `stt.target_status`, `stt.written` |
| `stt.attempt` | `workers/activities.py` | `stt.job_id`, `stt.scope`, `stt.attempt`, `stt.outcome` |
| `stt.provider.transcribe` | provider adapters | `stt.provider`, `stt.operation`, `stt.outcome`, `stt.model` |
| `stt.provider.poll`, `stt.provider.cancel` | managed adapters | same, `stt.provider_job_id` |
| `stt.storage.put` / `get` / `presign` | storage layer | `stt.operation`, bucket, size |
| `stt.audio.validate` | audio validation | `stt.duration_s`, format, codec, channels |

The job id is attached to every span that can see it (context variable, so
nested spans inherit it), which is what makes a single job traceable across
processes.

**Propagation across the Temporal boundary** is real, not assumed: the
workflow and its activities continue the trace of the API request that
accepted the job (Temporal's `TracingInterceptor` with
`always_create_workflow_spans=True`). Verified twice — in-process
(`tests/integration/test_trace_propagation.py`) and between real processes
by `scripts/e2e_phase5.py`, which compares the API's request trace id with
the trace ids the worker exports (`api->worker propagated=True`).

> FastAPI 0.133 ships its own telemetry (`fastapi/telemetry/`). With a
> tracer provider installed it adds `fastapi.endpoint`,
> `fastapi.dependencies` and `fastapi.serialization` child spans, plus
> `http_server_*` metrics of its own. They are harmless extra context; our
> dashboard and alerts use only `stt_*` names so the two never mix.

## Logs

Default output is human-readable text. `STT_LOG_JSON=true` switches every
record to a single redacted JSON object:

```json
{"timestamp": "2026-10-11T14:56:38.539021+00:00", "level": "INFO",
 "logger": "stt_platform.access", "service": "stt-platform",
 "message": "POST /v1/transcriptions -> 202", "correlation_id": "e2e-phase5",
 "trace_id": "9776988aa1128559188ff4d64fe7cbdd", "span_id": "1f0f5f6a1c4b1d0e",
 "job_id": "f39e1ed6-7543-4c10-850b-b79ae2e3388b", "duration_ms": 263.75}
```

* `correlation_id` is issued **always** (from `X-Request-ID` when the client
  sends one, otherwise generated) — it is support value, not telemetry;
* `trace_id`/`span_id` appear when the record is written inside a span;
* `job_id` is filled in automatically for records emitted by an activity;
* the access log line is written for every request, including health probes.

**Redaction** (`observability/redaction.py`) runs on every JSON record and
on span attributes: key-shaped material (API keys, bearer tokens,
passwords, secrets, signatures) is replaced with `***`, whether it appears
as a field value or inside a message string. The e2e asserts that no API
key from `backend/.env` appears anywhere the processes wrote.

## Metrics

Names, types and labels (all `stt_`-prefixed, so they can never collide
with the FastAPI ones):

| Metric | Type | Unit | Labels | Recorded by |
| --- | --- | --- | --- | --- |
| `stt_api_requests_total` | counter | 1 | `http_route`, `http_request_method`, `http_response_status_code` | API |
| `stt_api_request_duration_seconds` | histogram | s | same | API |
| `stt_api_errors_total` | counter | 1 | same (status ≥ 400) | API |
| `stt_upload_bytes` | histogram | By | `content_type` | API |
| `stt_job_queue_wait_seconds` | histogram | s | `provider` | worker (accepted → processing) |
| `stt_job_processing_seconds` | histogram | s | `provider` | worker (per attempt) |
| `stt_job_real_time_factor` | histogram | 1 | `provider` | worker (processing s per audio s) |
| `stt_job_end_to_end_seconds` | histogram | s | `provider`, `status` | worker (accepted → terminal) |
| `stt_job_attempts_total` | counter | 1 | `provider`, `phase` or `attempt` | worker |
| `stt_job_retries_total` | counter | 1 | `provider`, `error_code` | worker |
| `stt_job_fallbacks_total` | counter | 1 | `provider.requested`, `provider.actual` | worker |
| `stt_provider_calls_total` | counter | 1 | `provider`, `operation`, `outcome` | worker |
| `stt_provider_duration_seconds` | histogram | s | same | worker |
| `stt_provider_errors_total` | counter | 1 | same (outcome ≠ ok) | worker |
| `stt_provider_throttled_total` | counter | 1 | same (outcome = throttled) | worker |
| `stt_storage_errors_total` | counter | 1 | `operation`, `error_type` | API + worker |
| `stt_queue_depth` | gauge | 1 | — | both (pending outbox dispatches) |
| `stt_process_cpu_percent` | gauge | % | — | both |
| `stt_process_memory_bytes` | gauge | By | — | both |
| `stt_gpu_utilization_percent` | gauge | % | — | both (absent when no GPU) |
| `stt_gpu_memory_bytes` | gauge | By | — | both (absent when no GPU) |

Histograms expose the usual `_bucket`, `_count` and `_sum` series, which is
why the dashboard and the alerts reference e.g.
`stt_job_end_to_end_seconds_count`.

### Where each series lives

Metrics are **per process**. The API serves `GET /metrics` from its own
registry; the worker — which has no HTTP server of its own — starts the
`prometheus_client` thread on `STT_PROMETHEUS_PORT`. API metrics exist only
in the API process, job and provider metrics only in the worker, and the
runtime gauges in both. Scrape both:

```yaml
scrape_configs:
  - job_name: stt-api
    static_configs: [{targets: ["127.0.0.1:9466"]}]
  - job_name: stt-worker
    static_configs: [{targets: ["127.0.0.1:9467"]}]
```

Give the two processes different ports. On Windows two sockets can bind the
same port without an error, and the loser silently serves nothing.

### Which path a job takes

A file shorter than `STT_MAX_SYNC_DURATION_S` (default 180 s) is transcribed
**inside the API request** — no workflow, no worker, so no `stt_job_*`
series and no Temporal spans, by design. Longer files (and any provider that
requires async) go through the outbox → Temporal → worker path, where the
job metrics live. The Phase-5 e2e sets `STT_MAX_SYNC_DURATION_S=0` to force
the queued path, because that is where most of this phase's instrumentation
actually fires.

## Dashboard and alerts

* `backend/src/stt_platform/observability/dashboards/stt-overview.json` —
  Grafana dashboard, 20 panels: request rate/latency/errors, upload size,
  queue wait, end-to-end p95 by provider, processing p95, real-time factor,
  attempts vs retries, fallbacks, provider calls/latency/errors/throttling,
  storage errors, queue depth, process CPU/memory, GPU utilisation/memory.
* `backend/src/stt_platform/observability/alerts/stt-alerts.yaml` —
  Prometheus rules, 18 alerts: API error rate and latency, upload size
  spike, jobs failing, queue backlog, queue wait, real-time-factor
  degradation, slow processing, attempts without success, retry storm,
  provider fallbacks, provider error rate, provider throttling, storage
  errors, process CPU/memory, GPU memory, GPU saturation.

Both are validated by `tests/unit/test_prometheus_artifacts.py`: every
metric referenced must exist in `names.ALL`, so a renamed metric breaks the
build instead of the dashboard.

## Verifying it works

```bash
cd backend
uv run pytest -q                       # unit + integration
uv run pytest -m gpu -q                # GPU marker
uv run python ../scripts/e2e_phase5.py # real API + worker + Temporal, one job
```

The e2e leaves its evidence in `results/phase5_e2e/`: `result.json` (status
trail, metrics values, span names, trace ids, redaction result) and the two
scrape snapshots `metrics_api.txt` / `metrics_worker.txt`.

## Known limits

* **Cost estimates are not implemented.** The spec asks for them "when
  reliable"; we have no dependable price source — vendor pricing changes and
  our own live runs cover a single 10 s sample per provider — so the honest
  position is to ship the inputs (upload size, provider durations, provider
  identity) and no invented money figure.
* The console exporter is for verification; in production point
  `STT_OTEL_ENDPOINT` at a collector.
* Trace and metric retention is whatever the collector/backend keeps — this
  repo ships no storage of its own.
* The runtime sampler reads the GPU through NVML; without a GPU those two
  gauges are simply absent rather than zero.
* Spans carry ids, sizes and durations. Audio and transcript text are never
  attached, so a trace cannot be used to recover customer content.
