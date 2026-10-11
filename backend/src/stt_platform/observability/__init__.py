"""Observability: tracing, metrics, structured logs, dashboards.

Import surface for the rest of the platform. Nothing here is imported at
module scope by the app's hot paths except the thin ``get_tracer`` /
``get_meter`` accessors, and both are no-ops when ``STT_OTEL_ENABLED`` is
false.
"""
from stt_platform.observability.tracing import (
    active_span_context,
    get_tracer,
    setup_tracing,
    shutdown_tracing,
)

__all__ = [
    "active_span_context",
    "get_tracer",
    "setup_tracing",
    "shutdown_tracing",
]
