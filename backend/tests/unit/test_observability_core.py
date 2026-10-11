"""Observability core: tracing provider, no-op mode, redaction.

Everything is asserted through the SDK's in-memory exporter and a
recording meter — no network, no collector.
"""
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import pytest

from stt_platform.core.config import Settings
from stt_platform.observability import tracing
from stt_platform.observability.redaction import redact_mapping, redact_text


@pytest.fixture(autouse=True)
def _clean_provider():
    """Each test starts and ends with no provider installed."""
    tracing.shutdown_tracing()
    yield
    tracing.shutdown_tracing()


def _settings(**overrides) -> Settings:
    base = {
        "otel_enabled": True,
        "otel_exporter": "none",
        "otel_service_name": "stt-platform-test",
        "otel_environment": "unit",
    }
    base.update(overrides)
    return Settings(**base)


# --------------------------------------------------------------------- #
# tracing
# --------------------------------------------------------------------- #


def test_disabled_mode_builds_no_provider():
    provider = tracing.setup_tracing(_settings(otel_enabled=False))

    assert provider is None
    assert tracing.active_provider() is None
    # A tracer obtained anyway must still be usable (no-op path).
    with tracing.get_tracer().start_as_current_span("noop") as span:
        span.set_attribute("x", 1)


def test_enabled_provider_carries_resource_attributes():
    provider = tracing.setup_tracing(_settings())
    try:
        attributes = provider.resource.attributes
        assert attributes["service.name"] == "stt-platform-test"
        assert attributes["deployment.environment"] == "unit"
        assert "service.version" in attributes
    finally:
        tracing.shutdown_tracing()


def test_spans_are_exported_with_attributes_and_parenting():
    exporter = InMemorySpanExporter()
    provider = tracing.setup_tracing(
        _settings(), span_exporter=exporter
    )
    try:
        with tracing.get_tracer().start_as_current_span("job.run") as span:
            span.set_attribute("stt.job_id", "job-1")
            with tracing.get_tracer().start_as_current_span("provider.call"):
                pass
    finally:
        tracing.shutdown_tracing()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert set(spans) == {"job.run", "provider.call"}
    assert spans["job.run"].attributes["stt.job_id"] == "job-1"
    child = spans["provider.call"]
    parent = spans["job.run"]
    assert child.parent is not None
    assert child.parent.span_id == parent.context.span_id
    assert child.context.trace_id == parent.context.trace_id


def test_active_span_context_exposes_trace_and_span_ids():
    exporter = InMemorySpanExporter()
    tracing.setup_tracing(_settings(), span_exporter=exporter)
    try:
        assert tracing.active_span_context() is None
        with tracing.get_tracer().start_as_current_span("with-context"):
            context = tracing.active_span_context()
            assert context is not None
            trace_id, span_id = context
            assert len(trace_id) == 32 and len(span_id) == 16
            assert int(trace_id, 16) > 0
        assert tracing.active_span_context() is None
    finally:
        tracing.shutdown_tracing()


def test_unreachable_exporter_never_raises():
    settings = _settings(
        otel_exporter="otlp", otel_endpoint="http://127.0.0.1:1"
    )
    provider = tracing.setup_tracing(settings)
    try:
        with tracing.get_tracer().start_as_current_span("to-nowhere"):
            pass
    finally:
        tracing.shutdown_tracing()  # must not raise either


def test_setup_is_idempotent():
    first = tracing.setup_tracing(_settings())
    second = tracing.setup_tracing(_settings())
    try:
        assert first is second
    finally:
        tracing.shutdown_tracing()


# --------------------------------------------------------------------- #
# redaction
# --------------------------------------------------------------------- #


def test_redact_text_masks_key_like_tokens():
    cases = [
        "auth failed for sk_28tf23et_EzOVj0LW9w82m80Su3GHpz0Q",
        "header Authorization: Bearer abc123def456ghi",
        "api-subscription-key: 2bfca53da24553bfeb8b06293275b6ab5434ccc8",
        "api_key=supersecretvalue123",
        'password: "hunter2hunter2"',
    ]
    for raw in cases:
        redacted = redact_text(raw)
        assert "***" in redacted, raw
        for secret in (
            "sk_28tf23et_EzOVj0LW9w82m80Su3GHpz0Q",
            "abc123def456ghi",
            "2bfca53da24553bfeb8b06293275b6ab5434ccc8",
            "supersecretvalue123",
            "hunter2hunter2",
        ):
            assert secret not in redacted, raw


def test_redact_text_keeps_ordinary_content():
    for text in (
        "job b227737f-7b2e-4a1d-9f0e-2f0a1b2c3d4e SUCCEEDED in 18.1s",
        "provider faster-whisper returned 26 words",
        "rtf=0.25 attempts=2",
    ):
        assert redact_text(text) == text


def test_redact_mapping_drops_content_fields():
    payload = {
        "job_id": "job-1",
        "provider": "deepgram",
        "transcript": "hello world",
        "audio_bytes": b"RIFF....",
        "text": "hello world",
        "file": "/tmp/x.wav",
        "authorization": "Token abc123def",
        "api_key": "sk_live_abcdef123456",
        "nested": {"transcript": "inner", "attempt": 1},
    }

    redacted = redact_mapping(payload)

    assert redacted["job_id"] == "job-1"
    assert redacted["provider"] == "deepgram"
    assert redacted["nested"] == {"attempt": 1}
    for gone in ("transcript", "audio_bytes", "text", "file"):
        assert gone not in redacted
    assert "***" in redacted["authorization"]
    assert "***" in redacted["api_key"]
