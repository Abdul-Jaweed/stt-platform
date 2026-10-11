"""Tracing setup: one provider, off by default, never fatal.

``setup_tracing`` builds the SDK ``TracerProvider`` with a resource that
identifies the service and environment, attaches exactly one span
processor (memory in tests, console or OTLP/HTTP otherwise), and installs
it globally. When ``otel_enabled`` is false it returns ``None`` and the
global provider stays the API's no-op implementation, so instrumentation
call sites stay identical in both modes.
"""
from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanProcessor,
)
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.trace import format_span_id, format_trace_id

from stt_platform.core.config import Settings
from stt_platform.observability import context as obs_context

logger = logging.getLogger(__name__)

SERVICE_VERSION = "0.1.0"

class _State:
    provider: TracerProvider | None = None


_STATE = _State()


def build_resource(settings: Settings) -> Resource:
    return Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": SERVICE_VERSION,
            "deployment.environment": settings.otel_environment,
        }
    )


def build_exporter(settings: Settings) -> SpanExporter | None:
    """Exporter for the configured mode; never raises on bad config."""
    if settings.otel_exporter == "console":
        return ConsoleSpanExporter()
    if settings.otel_exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter(endpoint=f"{settings.otel_endpoint}/v1/traces")
    return None


class ContextAttributeSpanProcessor(SpanProcessor):
    """Stamp every span with the request/job ids currently in scope.

    Without this, a span only carries what the call site remembered to set;
    with it, every span in a request — service, repository, provider — is
    filterable by correlation id and by job id.
    """

    def on_start(
        self, span: Span, parent_context: Context | None = None
    ) -> None:
        correlation_id = obs_context.current_correlation_id()
        if correlation_id:
            span.set_attribute("stt.correlation_id", correlation_id)
        job_id = obs_context.current_job_id()
        if job_id:
            span.set_attribute("stt.job_id", job_id)

    def on_end(self, span: ReadableSpan) -> None:
        return None


def setup_tracing(
    settings: Settings, *, span_exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """Install the global tracer provider; idempotent per process."""
    if not settings.otel_enabled:
        return None
    if _STATE.provider is not None:
        return _STATE.provider

    provider = TracerProvider(
        resource=build_resource(settings),
        sampler=TraceIdRatioBased(settings.otel_sample_ratio),
    )
    provider.add_span_processor(ContextAttributeSpanProcessor())
    exporter = span_exporter or build_exporter(settings)
    if exporter is not None:
        processor: Any = (
            SimpleSpanProcessor(exporter)
            if span_exporter is not None
            else BatchSpanProcessor(exporter)
        )
        provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    _STATE.provider = provider
    logger.debug(
        "tracing enabled: exporter=%s endpoint=%s",
        settings.otel_exporter,
        settings.otel_endpoint,
    )
    return provider


def shutdown_tracing() -> None:
    """Flush and drop the provider; safe to call when tracing is off."""
    provider, _STATE.provider = _STATE.provider, None
    if provider is None:
        return
    try:
        provider.shutdown()
    except Exception:
        logger.debug("tracer provider shutdown failed", exc_info=True)


def active_provider() -> TracerProvider | None:
    """The provider this process installed, or ``None`` when tracing is off.

    Instrumentation that can take an explicit provider (FastAPI, Temporal)
    receives this one rather than reading the global, because the OTel API
    refuses to replace an already-installed global provider — which would
    make a re-configured process (and every test) silently keep the first
    provider's exporters.
    """
    return _STATE.provider


def get_tracer(name: str = "stt_platform") -> trace.Tracer:
    """Tracer from the installed provider (no-op when tracing is off)."""
    provider = _STATE.provider
    if provider is not None:
        return provider.get_tracer(name)
    return trace.get_tracer(name)


def active_span_context() -> tuple[str, str] | None:
    """``(trace_id, span_id)`` as hex strings, or ``None`` without a span."""
    span = trace.get_current_span()
    context = span.get_span_context()
    if context is None or not context.is_valid:
        return None
    return format_trace_id(context.trace_id), format_span_id(context.span_id)
