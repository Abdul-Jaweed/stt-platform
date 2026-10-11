"""Worker-side observability: real PostgreSQL + RustFS, fake provider.

The activity is the only place that knows what a transcription actually
cost (provider latency, audio seconds, outcome), so the spans and metrics
asserted here are the ones the dashboards depend on.
"""
from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stt_platform.core.config import Settings
from stt_platform.observability import metrics, tracing
from stt_platform.persistence.models import Base
from stt_platform.workers.activities import TranscriptionActivities
from tests.fakes import FakeAsyncProvider, FakeProvider
from tests.integration.harness import build_deps, build_store, make_settings, seed_job
from tests.rustfs_container import RustFSContainer


@pytest.fixture(scope="module")
def rustfs():
    with RustFSContainer() as rc:
        yield rc


@pytest.fixture(scope="module")
def store(rustfs):
    return build_store(rustfs)


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(pg_engine):
    Base.metadata.drop_all(pg_engine)
    Base.metadata.create_all(pg_engine)
    return sessionmaker(bind=pg_engine, future=True)


@pytest.fixture()
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture()
def observability():
    span_exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    obs_settings = Settings(
        otel_enabled=True,
        otel_exporter="none",
        database_url="postgresql+psycopg://stt:stt@127.0.0.1:25432/stt",
    )
    tracing.setup_tracing(obs_settings, span_exporter=span_exporter)
    metrics.setup_metrics(obs_settings, reader=reader)
    yield span_exporter, reader
    tracing.shutdown_tracing()
    metrics.shutdown_metrics()


def _points(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    if data is None:
        return []
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == name:
                    return list(metric.data.data_points)
    return []


def _find(points: list, **attributes):
    for point in points:
        if all(point.attributes.get(k) == v for k, v in attributes.items()):
            return point
    return None


def test_sync_attempt_emits_spans_and_provider_metrics(
    session_factory, store, settings, observability
):
    span_exporter, reader = observability
    deps = build_deps(session_factory, FakeProvider(), store, settings)
    job_id = seed_job(session_factory, store, provider="fake")

    result = TranscriptionActivities(deps).execute_attempt(job_id, "local", 1)

    assert result["outcome"] == "ok"
    spans = {span.name: span for span in span_exporter.get_finished_spans()}
    attempt = spans["stt.attempt"]
    assert attempt.attributes["stt.job_id"] == job_id
    assert attempt.attributes["stt.attempt"] == 1
    assert attempt.attributes["stt.outcome"] == "ok"
    assert attempt.status.status_code is StatusCode.UNSET
    transcribe = spans["stt.provider.transcribe"]
    assert transcribe.attributes["stt.provider"] == "fake"
    assert transcribe.attributes["stt.operation"] == "transcribe"
    assert transcribe.parent is not None
    assert transcribe.parent.span_id == attempt.context.span_id

    calls = _points(reader, "stt_provider_calls_total")
    assert _find(calls, provider="fake", operation="transcribe", outcome="ok").value == 1
    duration = _points(reader, "stt_provider_duration_seconds")
    assert _find(duration, provider="fake", operation="transcribe").count == 1
    assert _points(reader, "stt_provider_errors_total") == []

    # The fake reports 0.1 s of processing for 1.0 s of audio.
    rtf = _points(reader, "stt_job_real_time_factor")
    assert _find(rtf, provider="fake").count == 1
    processing = _points(reader, "stt_job_processing_seconds")
    assert _find(processing, provider="fake").count == 1


def test_failed_attempt_marks_span_and_counts_errors(
    session_factory, store, settings, observability
):
    span_exporter, reader = observability
    deps = build_deps(
        session_factory, FakeProvider(fail="transient"), store, settings
    )
    job_id = seed_job(session_factory, store, provider="fake")

    result = TranscriptionActivities(deps).execute_attempt(job_id, "local", 1)

    assert result["outcome"] == "transient"
    spans = {span.name: span for span in span_exporter.get_finished_spans()}
    attempt = spans["stt.attempt"]
    assert attempt.attributes["stt.outcome"] == "transient"
    assert attempt.status.status_code is StatusCode.ERROR

    calls = _points(reader, "stt_provider_calls_total")
    failed = _find(calls, provider="fake", operation="transcribe", outcome="error")
    assert failed is not None and failed.value == 1
    errors = _points(reader, "stt_provider_errors_total")
    assert _find(errors, provider="fake", operation="transcribe").value == 1


def test_async_submit_emits_submit_span(
    session_factory, store, settings, observability
):
    span_exporter, reader = observability
    deps = build_deps(session_factory, FakeAsyncProvider(), store, settings)
    job_id = seed_job(session_factory, store, provider="fake-async")

    result = TranscriptionActivities(deps).execute_attempt(job_id, "local", 1)

    assert result["outcome"] == "pending"
    spans = {span.name: span for span in span_exporter.get_finished_spans()}
    submit = spans["stt.provider.submit"]
    assert submit.attributes["stt.provider"] == "fake-async"
    calls = _points(reader, "stt_provider_calls_total")
    assert _find(calls, provider="fake-async", operation="submit", outcome="ok").value == 1
