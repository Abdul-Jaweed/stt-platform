"""Temporal tracing: interceptors and activity-level domain spans.

The SDK's ``TracingInterceptor`` already opens a span per workflow/activity
run and propagates context across the boundary; what it cannot know is what
the *work* was. The spans opened here are the domain ones — which provider
was called, with which audio, and how it ended.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from temporalio.contrib.opentelemetry import TracingInterceptor

from stt_platform.core.config import Settings
from stt_platform.observability import tracing


def temporal_interceptors(settings: Settings) -> list[TracingInterceptor]:
    """Client/worker interceptors; empty when observability is off.

    The interceptor is both a client and a worker interceptor, so the same
    list is passed to ``Client.connect`` and to ``Worker``: the client side
    starts the workflow span, the worker side continues it.
    """
    if not settings.otel_enabled:
        return []
    return [
        TracingInterceptor(
            tracing.get_tracer(), always_create_workflow_spans=True
        )
    ]


@contextmanager
def activity_span(
    name: str, attributes: Mapping[str, Any] | None = None
) -> Iterator[Span | None]:
    """Open a domain span, or yield ``None`` when tracing is off.

    The span is made current for the block so nested spans (provider call
    inside attempt, storage inside attempt) attach as children; exceptions
    are recorded and marked on the span, then re-raised — the activities
    rely on their own exception taxonomy for retry decisions.
    """
    span: Span | None = None
    token = None
    if tracing.active_provider() is not None:
        span = tracing.get_tracer().start_span(
            name,
            kind=SpanKind.INTERNAL,
            attributes=dict(attributes or {}),
        )
        token = otel_context.attach(otel_trace.set_span_in_context(span))
    try:
        yield span
    except Exception as exc:
        if span is not None:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR))
        raise
    finally:
        if token is not None:
            otel_context.detach(token)
        if span is not None:
            span.end()
