"""Prometheus: an env-gated reader, registry, and scrape endpoint.

Off by default like everything else here. When enabled, the API serves
``/metrics`` from a registry we own (so tests and the worker can render the
same text), and the worker — which has no HTTP server of its own — starts
the prometheus_client thread on ``prometheus_port``.
"""
from __future__ import annotations

import logging

from opentelemetry.sdk.metrics.export import MetricReader

from stt_platform.core.config import Settings

log = logging.getLogger(__name__)

METRICS_PATH = "/metrics"


def build_prometheus_reader(
    settings: Settings,
) -> tuple[MetricReader | None, object | None]:
    """``(reader, registry)`` when enabled, ``(None, None)`` otherwise.

    The registry is ours so the endpoint can render exactly what the meter
    provider recorded, with no access to exporter internals.
    """
    if not settings.prometheus_enabled:
        return None, None
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    from prometheus_client import CollectorRegistry

    registry = CollectorRegistry()
    return PrometheusMetricReader(registry=registry), registry


def render_metrics(registry: object) -> bytes:
    """Prometheus text exposition for the given registry."""
    from prometheus_client import generate_latest

    return generate_latest(registry)  # type: ignore[arg-type]


def start_prometheus_server(settings: Settings, registry: object) -> bool:
    """Serve /metrics from a background thread (worker process).

    Returns ``True`` when a server was started. A bind failure is logged
    and swallowed: metrics must never stop transcription work.
    """
    if registry is None:
        return False
    try:
        from prometheus_client import start_http_server

        start_http_server(settings.prometheus_port, registry=registry)  # type: ignore[arg-type]
    except Exception:
        log.warning(
            "prometheus endpoint could not start on port %d",
            settings.prometheus_port,
            exc_info=True,
        )
        return False
    log.info(
        "prometheus metrics on http://127.0.0.1:%d%s",
        settings.prometheus_port,
        METRICS_PATH,
    )
    return True
