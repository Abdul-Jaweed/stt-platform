"""Structured logs: JSON lines, redacted, with request/job correlation.

Plain text stays the local default (readable in a terminal); ``log_json``
switches the root handler to one JSON object per line for aggregation. The
formatter pulls the correlation id and job id from the request/job context,
so a log line never has to remember to pass them — and every string that
leaves the process goes through the redactor first.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.trace import format_span_id, format_trace_id

from stt_platform.core.config import Settings
from stt_platform.observability import context
from stt_platform.observability.redaction import redact_mapping, redact_text

# Fields a record may carry that are worth shipping; anything else a caller
# puts in ``extra`` is dropped rather than guessed at.
_STRUCTURED_KEYS = (
    "job_id",
    "scope",
    "provider",
    "model",
    "attempt",
    "status",
    "outcome",
    "code",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "queue",
    "error_type",
)

_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}


class ContextFilter(logging.Filter):
    """Copy the current request/job ids onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "correlation_id", None):
            record.correlation_id = context.current_correlation_id()
        if not getattr(record, "job_id", None):
            record.job_id = context.current_job_id()
        return True


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line; every string redacted."""

    def __init__(self, service_name: str = "stt-platform") -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service_name,
            "message": redact_text(record.getMessage()),
        }
        # A log line joins its trace: same ids as the exported span.
        span_context = otel_trace.get_current_span().get_span_context()
        if span_context.is_valid:
            payload["trace_id"] = format_trace_id(span_context.trace_id)
            payload["span_id"] = format_span_id(span_context.span_id)
        correlation_id = getattr(record, "correlation_id", None)
        if not correlation_id:
            correlation_id = context.current_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        job_id = getattr(record, "job_id", None)
        if not job_id:
            job_id = context.current_job_id()
        if job_id:
            payload["job_id"] = job_id

        extras = {
            key: getattr(record, key)
            for key in _STRUCTURED_KEYS
            if getattr(record, key, None) is not None
        }
        if extras:
            payload.update(redact_mapping(extras))

        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(settings: Settings) -> None:
    """Install the JSON handler when asked; idempotent, never destructive.

    Repeated calls (uvicorn reload, tests) must not stack handlers, and
    turning JSON off must not silently remove handlers the embedding
    process configured.
    """
    root = logging.getLogger()
    if not settings.log_json:
        return
    for handler in root.handlers:
        if getattr(handler, "_stt_json", False):
            return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter(service_name=settings.otel_service_name))
    handler.addFilter(ContextFilter())
    handler._stt_json = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
