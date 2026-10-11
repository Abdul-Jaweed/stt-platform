"""Temporal tracing wiring: interceptors and activity span helper."""
from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from stt_platform.core.config import Settings
from stt_platform.observability import tracing
from stt_platform.observability.temporal import activity_span, temporal_interceptors


def _settings(*, otel_enabled: bool) -> Settings:
    return Settings(
        otel_enabled=otel_enabled,
        otel_exporter="none",
        database_url="postgresql+psycopg://stt:stt@127.0.0.1:25432/stt",
    )


@pytest.fixture(autouse=True)
def _clean_provider():
    tracing.shutdown_tracing()
    yield
    tracing.shutdown_tracing()


def test_interceptors_absent_when_observability_is_off():
    assert temporal_interceptors(_settings(otel_enabled=False)) == []


def test_interceptors_present_when_observability_is_on():
    interceptors = temporal_interceptors(_settings(otel_enabled=True))

    assert len(interceptors) == 1
    assert type(interceptors[0]).__name__ == "TracingInterceptor"


def test_activity_span_is_a_noop_without_a_provider():
    with activity_span("stt.attempt", {"stt.job_id": "job-1"}) as span:
        assert span is None


def test_activity_span_records_attributes_and_errors():
    exporter = InMemorySpanExporter()
    tracing.setup_tracing(_settings(otel_enabled=True), span_exporter=exporter)

    with pytest.raises(RuntimeError), activity_span(
        "stt.attempt", {"stt.job_id": "job-1"}
    ) as span:
        assert span is not None
        span.set_attribute("stt.outcome", "transient")
        raise RuntimeError("boom")

    (exported,) = exporter.get_finished_spans()
    assert exported.name == "stt.attempt"
    assert exported.attributes["stt.job_id"] == "job-1"
    assert exported.attributes["stt.outcome"] == "transient"
    assert exported.status.status_code is StatusCode.ERROR
    assert [event.name for event in exported.events] == ["exception"]
