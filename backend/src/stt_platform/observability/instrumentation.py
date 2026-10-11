"""API instrumentation: correlation ids, server spans, HTTP metrics.

One pure-ASGI middleware does all three in a single pass. It is written by
hand (rather than pulling in the FastAPI instrumentor) so the span names,
attribute keys, and metric labels are ours to pin in tests and stable
across OpenTelemetry semantic-convention versions.

Rules:
* the correlation id is issued **always** — it is support value, not
  telemetry, and it costs nothing when tracing is off;
* spans and metrics exist only when observability is enabled (no-op
  instruments otherwise);
* health probes are counted but never traced, so probe traffic does not
  dominate the trace stream.
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from stt_platform.observability import context, metrics, tracing

access_log = logging.getLogger("stt_platform.access")

HEALTH_PATHS = frozenset({"/v1/health/live", "/v1/health/ready"})
UNMATCHED_ROUTE = "<unmatched>"


def build_route_index(app: FastAPI) -> dict[int, str]:
    """Map ``id(route)`` -> full path template, prefixes included.

    FastAPI wraps included routers (``_IncludedRouter`` in 0.14x) and the
    matched ``APIRoute`` then carries only the *inner* path — the include
    prefix lives on the wrapper. Walking the tree once at startup keeps the
    request path cheap and the label low-cardinality. Unknown shapes are
    skipped rather than guessed.
    """
    index: dict[int, str] = {}

    def walk(entries, prefix: str) -> None:
        for entry in entries:
            path = getattr(entry, "path", None)
            if isinstance(path, str):
                index[id(entry)] = f"{prefix}{path}"
                continue
            include_context = getattr(entry, "include_context", None)
            original = getattr(entry, "original_router", None)
            if include_context is not None and original is not None:
                inner_prefix = getattr(include_context, "prefix", "") or ""
                walk(getattr(original, "routes", []) or [], prefix + inner_prefix)
                continue
            inner = getattr(entry, "routes", None)
            if isinstance(inner, (list, tuple)):
                walk(inner, prefix + (getattr(entry, "prefix", "") or ""))

    walk(getattr(app, "routes", []) or [], "")
    return index


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _route_template(scope: Scope, route_index: dict[int, str] | None) -> str:
    """Route template (``/v1/transcriptions/{job_id}``), never a raw id."""
    route = scope.get("route")
    if route is not None and route_index:
        template = route_index.get(id(route))
        if template:
            return template
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return UNMATCHED_ROUTE


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp, route_index: dict[int, str] | None = None):
        self.app = app
        self.route_index = route_index

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = _header(scope, b"x-request-id") or uuid.uuid4().hex
        token = context.set_correlation_id(correlation_id)
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = correlation_id
            await send(message)

        span = None
        if tracing.active_provider() is not None and path not in HEALTH_PATHS:
            span = tracing.get_tracer().start_span(
                f"{method} {path}", kind=SpanKind.SERVER
            )
        try:
            if span is not None:
                with trace.use_span(span, end_on_exit=False):
                    await self.app(scope, receive, send_wrapper)
            else:
                await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if span is not None:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
            raise
        finally:
            route = _route_template(scope, self.route_index)
            duration_s = time.perf_counter() - started
            if span is not None:
                span.update_name(f"{method} {route}")
                span.set_attribute("http.request.method", method)
                span.set_attribute("http.route", route)
                span.set_attribute("url.path", path)
                span.set_attribute("http.response.status_code", status_code)
                if status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                span.end()
            metrics.record_api_request(
                route=route,
                method=method,
                status=status_code,
                duration_s=duration_s,
            )
            # Logged before the context is reset so the line carries the id.
            access_log.info(
                "%s %s -> %s",
                method,
                route,
                status_code,
                extra={
                    "correlation_id": correlation_id,
                    "method": method,
                    "path": route,
                    "status_code": status_code,
                    "duration_ms": round(duration_s * 1000, 2),
                },
            )
            context.reset_correlation_id(token)


def instrument_app(app: FastAPI) -> None:
    """Attach the middleware. Safe to call when observability is off."""
    app.add_middleware(
        ObservabilityMiddleware, route_index=build_route_index(app)
    )
