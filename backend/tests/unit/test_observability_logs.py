"""Structured logging: JSON lines, redaction, correlation ids."""
from __future__ import annotations

import json
import logging

import pytest
from opentelemetry.trace import format_span_id, format_trace_id

from stt_platform.core.config import Settings
from stt_platform.observability import context
from stt_platform.observability.logging import (
    ContextFilter,
    JsonLogFormatter,
    setup_logging,
)


def _record(message: str, **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="stt.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@pytest.fixture(autouse=True)
def _clean_provider():
    from stt_platform.observability import tracing

    tracing.shutdown_tracing()
    yield
    tracing.shutdown_tracing()


def _payload(record: logging.LogRecord) -> dict:
    return json.loads(JsonLogFormatter().format(record))


def test_json_line_carries_level_logger_and_message():
    payload = _payload(_record("job accepted"))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "stt.test"
    assert payload["service"] == "stt-platform"
    assert payload["message"] == "job accepted"
    assert payload["timestamp"].endswith("+00:00")


def test_log_line_joins_the_active_trace():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from stt_platform.observability import tracing

    exporter = InMemorySpanExporter()
    tracing.setup_tracing(
        _settings(log_json=False, otel_enabled=True), span_exporter=exporter
    )
    try:
        with tracing.get_tracer().start_as_current_span("job.run") as span:
            payload = _payload(_record("inside the span"))
        (exported,) = exporter.get_finished_spans()
    finally:
        tracing.shutdown_tracing()

    assert payload["trace_id"] == format_trace_id(
        exported.context.trace_id
    )
    assert payload["span_id"] == format_span_id(exported.context.span_id)
    assert span.is_recording() is False

    # Outside any span the fields are absent rather than null.
    assert "trace_id" not in _payload(_record("outside"))


def test_correlation_and_job_ids_come_from_context():
    token_correlation = context.set_correlation_id("corr-123")
    token_job = context.set_job_id("job-456")
    try:
        payload = _payload(_record("processing"))
    finally:
        context.reset_job_id(token_job)
        context.reset_correlation_id(token_correlation)

    assert payload["correlation_id"] == "corr-123"
    assert payload["job_id"] == "job-456"

    # ... and are absent (not null) once the request is over.
    after = _payload(_record("done"))
    assert "correlation_id" not in after
    assert "job_id" not in after


def test_structured_extras_are_kept_and_unknown_extras_dropped():
    record = _record(
        "attempt failed",
        provider="deepgram",
        attempt=2,
        code="RATE_LIMITED",
        something_private="not shipped",
    )
    payload = _payload(record)

    assert payload["provider"] == "deepgram"
    assert payload["attempt"] == 2
    assert payload["code"] == "RATE_LIMITED"
    assert "something_private" not in payload


def test_secrets_are_redacted_in_message_and_extras():
    record = _record(
        "provider call with api_key=supersecret123 and Bearer abcdef123456",
        provider="deepgram",
        model="sk_livekey123456789",
    )
    payload = _payload(record)

    assert "supersecret123" not in payload["message"]
    assert "abcdef123456" not in payload["message"]
    assert "sk_livekey123456789" not in payload["model"]


def test_exception_text_is_included_and_redacted():
    try:
        raise ValueError("bad token=topsecretvalue")
    except ValueError:
        import sys

        record = _record("boom")
        record.exc_info = sys.exc_info()
        payload = _payload(record)

    assert "ValueError" in payload["exception"]
    assert "topsecretvalue" not in payload["exception"]


def test_context_filter_fills_missing_ids():
    record = _record("hi")
    token = context.set_correlation_id("corr-9")
    try:
        ContextFilter().filter(record)
    finally:
        context.reset_correlation_id(token)

    assert record.correlation_id == "corr-9"


def _settings(**overrides) -> Settings:
    base: dict[str, object] = dict(
        log_json=True,
        log_level="INFO",
        database_url="postgresql+psycopg://stt:stt@127.0.0.1:25432/stt",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture()
def root_handlers():
    root = logging.getLogger()
    before = list(root.handlers)
    yield root
    root.handlers = before


def test_setup_logging_installs_one_json_handler_and_is_idempotent(root_handlers):
    setup_logging(_settings())
    setup_logging(_settings())

    ours = [
        h for h in root_handlers.handlers if getattr(h, "_stt_json", False)
    ]
    assert len(ours) == 1
    assert isinstance(ours[0].formatter, JsonLogFormatter)


def test_setup_logging_leaves_plain_mode_alone(root_handlers):
    before = list(root_handlers.handlers)

    setup_logging(_settings(log_json=False))

    assert root_handlers.handlers == before
