# Phase 5 — Observability (OpenTelemetry, metrics, logs, dashboards)

Status: **complete** (T1–T8). Report: `docs/phase-5-report.md`. Operational
detail: `docs/observability.md`.

Spec: `IDEA.md` §"Observability" — "Use OpenTelemetry across API requests,
workflows, workers, audio processing, provider calls, and persistence.
Propagate trace context across asynchronous boundaries. Track API
latency/errors, upload size/duration, queue wait, end-to-end duration,
provider latency/errors/throttling, processing duration, real-time factor,
retries, fallbacks, queue depth, GPU/CPU/memory use, storage errors, and
cost estimates when reliable. Use structured logs with correlation IDs and
job IDs. Separate liveness from readiness so an optional provider outage
does not automatically make the whole service unready."

Privacy constraints that apply verbatim: never log audio bytes,
transcripts, or secrets; tenant isolation unchanged; a tracing outage must
never break a transcription.

## Design decisions

- **Off by default, never fatal.** `STT_OTEL_ENABLED=false` is the default:
  the app then uses a no-op provider (no exporters, no network). Any
  exporter failure is swallowed and logged once — observability must not
  become a new failure mode.
- **One package**: `stt_platform/observability/` (spec §layout) with
  `tracing.py`, `metrics.py`, `logging.py`, `redaction.py`,
  `instrumentation.py` (FastAPI + job pipeline), `runtime.py`
  (CPU/RSS/GPU/queue-depth sampler), `names.py` (the canonical metric-name
  registry that dashboards/alerts are validated against).
- **Metric names are constants** in `names.py`; a test parses the Grafana
  dashboard and the alert rules and fails if they reference a metric that
  is not in the registry (and vice versa).
- **Trace context propagation** across the async boundary uses
  `temporalio.contrib.opentelemetry.TracingInterceptor` on both the client
  and the worker.
- **Log records** carry `trace_id`/`span_id` (from the active span) plus
  `job_id`/`correlation_id` (contextvars bound by the API middleware and
  the worker loop). A redaction filter scrubs key-like tokens.
- **GPU metrics are optional**: `pynvml` is imported lazily; on a machine
  without an NVIDIA driver the sampler records CPU/RSS only and warns once.

## Dependencies to add

`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`,
`opentelemetry-exporter-prometheus`, `opentelemetry-instrumentation-fastapi`,
`psutil`, `pynvml` (lazy). Dev: none new (SDK in-memory exporters are the
test doubles).

## Task 1 — Observability core: tracing provider, redaction, no-op mode

**Files:** `observability/__init__.py`, `tracing.py`, `redaction.py`,
`core/config.py` (+ keys), tests.

**Tests (unit):** provider resource attrs (`service.name`,
`service.version`, `deployment.environment`); spans exported to an
in-memory exporter when enabled; disabled → no spans exported and no
exporter constructed; `redact_text` masks `sk_…`/`Bearer …`/`api-key`
values while leaving job ids intact; `setup_tracing` never raises when the
exporter target is unreachable.

## Task 2 — API instrumentation: server spans, HTTP metrics, correlation ids

**Files:** `observability/instrumentation.py`, `main.py`, `api/deps.py`
(contextvar binding), tests.

**Tests (integration, TestClient + in-memory exporter + metric reader):**
one server span per request with `http.route`/`http.status_code`; request
counter/histogram recorded with route template (not raw path); upload size
recorded from `Content-Length`/file size; `X-Request-ID` accepted and
echoed, generated when absent; the id is bound to logs and the span;
errors (4xx/5xx) counted; `/v1/health/live` still answers when the tracer
is disabled.

## Task 3 — Job pipeline metrics + spans

**Files:** `observability/metrics.py` (+ job/provider instruments),
`services/jobs.py`, `workers/activities.py`, `providers/managed/http_base.py`,
`providers/faster_whisper.py`, tests.

**Tests (unit + integration with fake provider):** queue wait
(accepted→started), end-to-end duration, processing duration, RTF
(processing/audio duration, only when duration > 0), attempts/retries
counter, storage-error counter, provider call latency/error/throttling
counters with `provider`/`outcome` attributes, fallback counter stays 0
when nothing fell back (no invented fallbacks), span per provider call
with `provider`/`model` attributes and no audio bytes/transcript text.

## Task 4 — Trace context across the Temporal boundary

**Files:** `services/temporal_starter.py`, `workers/transcription_worker.py`,
`observability/tracing.py` (interceptor factory), tests.

**Tests:** unit — client and worker configs carry the
`TracingInterceptor`; integration (live Temporal, skipped when 7233 is
unreachable) — a workflow + its activity spans share the trace id of the
starting span, so one trace covers API → workflow → activity → provider.

## Task 5 — Structured logs with correlation and job ids

**Files:** `observability/logging.py`, `main.py`,
`workers/transcription_worker.py`, tests.

**Tests (unit):** JSON formatter emits `ts`, `level`, `logger`, `message`,
`service`, and injects `trace_id`/`span_id` when a span is active;
`job_id`/`correlation_id` come from contextvars; the redaction filter
scrubs key-like tokens and drops `transcript`/`audio` fields; `setup_logging`
is idempotent (no duplicate handlers) and never raises with a missing
trace context.

## Task 6 — Runtime sampler: CPU, RSS, GPU, queue depth

**Files:** `observability/runtime.py`, `workers/transcription_worker.py`,
`persistence/repositories.py` (queue-depth query), tests.

**Tests (unit):** CPU/RSS gauges recorded via psutil; GPU gauges recorded
when pynvml is available and *absent* (no crash, one warning) when it is
not; queue depth gauge equals the pending outbox count; the sampler thread
stops cleanly on shutdown.

## Task 7 — Prometheus endpoint, Grafana dashboard, alert rules

**Files:** `observability/prometheus.py` (env-gated `/metrics`),
`observability/dashboards/stt-overview.json`,
`observability/alerts/stt-alerts.yaml`, `names.py`, tests.

**Tests (unit):** dashboard JSON parses and every `expr` metric name is in
the registry; alert rules parse and reference only registry metrics; the
registry has no metric that the dashboard/alerts ignore (coverage both
ways); the Prometheus endpoint serves text/plain with `stt_` metrics when
enabled and 404s when disabled.

## Task 8 — Documentation + report + gates

**Files:** `docs/observability.md` (metric catalog, log fields, redaction
rules, sampling, propagation, dashboard/alert usage, how to point OTLP at
a collector), `docs/phase-5-report.md`, `README.md`, plan ticked.

**Steps:** full gates (`uv run pytest -q`, `-m gpu`, `ruff check`,
frontend `npm test` for the record) → commit
`docs(phase5): observability report and metric catalog (T8)`.

## Acceptance criteria

- OTel instrumentation exists for API requests, workflows, activities,
  audio processing, provider calls, and persistence (verified by tests
  asserting real spans/metrics, not by configuration alone).
- Trace context propagates API → workflow → activity.
- The spec's metric list is covered by named instruments (queue wait,
  end-to-end, provider latency/errors/throttling, processing duration, RTF,
  retries, queue depth, CPU/RSS/GPU, storage errors); "cost estimates when
  reliable" is honestly marked as not implemented (no reliable vendor
  pricing data) in the docs.
- Structured logs carry correlation + job ids; secrets/transcripts/audio
  bytes are never logged (redaction tested).
- Liveness ≠ readiness (already true; pinned by an existing test).
- Dashboards + alerts shipped as artifacts, validated against the metric
  registry.
- Everything is off by default and cannot break a transcription.
