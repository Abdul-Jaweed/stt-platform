"""API instrumentation: server spans, HTTP metrics, correlation ids.

Own ASGI middleware instead of the FastAPI instrumentor: the attribute
names, span naming, and metric labels are then ours to pin in tests
(and stable across semconv versions).
"""
import logging
import re

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from stt_platform.core.config import Settings
from stt_platform.observability import metrics, tracing
from stt_platform.providers.registry import ProviderRegistry


def _settings(**overrides) -> Settings:
    base = {
        "otel_enabled": True,
        "otel_exporter": "none",
        "otel_service_name": "stt-api-test",
        "database_url": "postgresql+psycopg://stt:stt@127.0.0.1:25432/stt",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def observability():
    """Fresh tracing + metrics providers with in-memory sinks."""
    tracing.shutdown_tracing()
    metrics.shutdown_metrics()
    span_exporter = InMemorySpanExporter()
    metric_reader = InMemoryMetricReader()
    yield span_exporter, metric_reader
    tracing.shutdown_tracing()
    metrics.shutdown_metrics()


@pytest.fixture()
def client_factory(observability):
    span_exporter, metric_reader = observability
    tracing.setup_tracing(_settings(), span_exporter=span_exporter)
    metrics.setup_metrics(_settings(), reader=metric_reader)
    from stt_platform.main import create_app

    def _make(settings: Settings | None = None) -> TestClient:
        app = create_app(settings or _settings(), registry=ProviderRegistry())
        return TestClient(app)

    return _make


def _spans_by_name(exporter: InMemorySpanExporter) -> dict:
    return {span.name: span for span in exporter.get_finished_spans()}


def _points(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    if data is None:  # nothing collected yet
        return []
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == name:
                    return list(metric.data.data_points)
    return []


def _attrs(point) -> dict:
    return dict(point.attributes)


# --------------------------------------------------------------------- #
# spans
# --------------------------------------------------------------------- #


def test_request_span_carries_route_and_status(client_factory, observability):
    span_exporter, _ = observability
    client = client_factory()

    response = client.get("/v1/providers")

    assert response.status_code == 200
    spans = _spans_by_name(span_exporter)
    assert "GET /v1/providers" in spans
    span = spans["GET /v1/providers"]
    assert span.attributes["http.request.method"] == "GET"
    assert span.attributes["http.route"] == "/v1/providers"
    assert span.attributes["http.response.status_code"] == 200
    assert span.attributes["url.path"] == "/v1/providers"
    assert span.kind.name == "SERVER"


def test_unmatched_route_gets_low_cardinality_span_name(
    client_factory, observability
):
    span_exporter, _ = observability
    client = client_factory()

    response = client.get("/v1/does-not-exist")

    assert response.status_code == 404
    spans = _spans_by_name(span_exporter)
    assert "GET <unmatched>" in spans
    assert spans["GET <unmatched>"].attributes["http.route"] == "<unmatched>"


def test_health_probes_are_not_traced(client_factory, observability):
    span_exporter, _ = observability
    client = client_factory()

    assert client.get("/v1/health/live").status_code == 200

    assert "GET /v1/health/live" not in _spans_by_name(span_exporter)


def test_span_is_not_exported_when_tracing_is_disabled(observability):
    span_exporter, metric_reader = observability
    from stt_platform.main import create_app

    # Nothing installed at all: the app must stay fully functional.
    tracing.shutdown_tracing()
    metrics.shutdown_metrics()
    app = create_app(_settings(otel_enabled=False), registry=ProviderRegistry())
    client = TestClient(app)

    assert client.get("/v1/providers").status_code == 200

    assert span_exporter.get_finished_spans() == ()
    assert _points(metric_reader, "stt_api_requests_total") == []


# --------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------- #


def test_request_counter_and_duration_recorded(client_factory, observability):
    _, metric_reader = observability
    client = client_factory()

    client.get("/v1/providers")
    client.get("/v1/providers")
    client.get("/v1/does-not-exist")

    points = _points(metric_reader, "stt_api_requests_total")
    by_status = {_attrs(p)["http.response.status_code"]: p.value for p in points}
    assert by_status == {200: 2, 404: 1}
    for point in points:
        attrs = _attrs(point)
        assert attrs["http.request.method"] == "GET"
        assert attrs["http.route"] in ("/v1/providers", "<unmatched>")

    durations = _points(metric_reader, "stt_api_request_duration_seconds")
    assert sum(p.count for p in durations) == 3
    assert all(p.sum >= 0 for p in durations)


def test_error_counter_only_counts_failures(client_factory, observability):
    _, metric_reader = observability
    client = client_factory()

    client.get("/v1/providers")
    client.get("/v1/does-not-exist")

    points = _points(metric_reader, "stt_api_errors_total")
    assert len(points) == 1
    attrs = _attrs(points[0])
    assert attrs["http.response.status_code"] == 404
    assert attrs["http.route"] == "<unmatched>"
    assert points[0].value == 1


# --------------------------------------------------------------------- #
# correlation ids
# --------------------------------------------------------------------- #


def test_correlation_id_echoed_and_bound_to_span(client_factory, observability):
    span_exporter, _ = observability
    client = client_factory()

    response = client.get(
        "/v1/providers", headers={"X-Request-ID": "req-abc-123"}
    )

    assert response.headers["X-Request-ID"] == "req-abc-123"
    span = _spans_by_name(span_exporter)["GET /v1/providers"]
    assert span.attributes["stt.correlation_id"] == "req-abc-123"


def test_correlation_id_generated_when_absent(client_factory):
    client = client_factory()

    response = client.get("/v1/providers")

    generated = response.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", generated)


def test_access_log_line_carries_request_context(client_factory, caplog):
    client = client_factory()

    with caplog.at_level(logging.INFO, logger="stt_platform.access"):
        response = client.get(
            "/v1/providers", headers={"X-Request-ID": "corr-access-1"}
        )

    assert response.status_code == 200
    record = next(
        r for r in caplog.records if r.name == "stt_platform.access"
    )
    assert record.correlation_id == "corr-access-1"
    assert record.method == "GET"
    assert record.path == "/v1/providers"
    assert record.status_code == 200
    assert record.duration_ms >= 0


def test_correlation_id_present_even_when_tracing_is_disabled(observability):
    from stt_platform.main import create_app

    tracing.shutdown_tracing()
    metrics.shutdown_metrics()
    app = create_app(_settings(otel_enabled=False), registry=ProviderRegistry())
    client = TestClient(app)

    response = client.get("/v1/providers")

    assert response.headers["X-Request-ID"]
