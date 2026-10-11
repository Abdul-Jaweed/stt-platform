"""Shipped observability artifacts must match the metric registry.

The dashboard and the alert rules are code: if they reference a metric the
platform never records (or if a recorded metric is silently ignored), the
failure is here rather than in a production incident.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from stt_platform.core.config import Settings
from stt_platform.main import create_app
from stt_platform.observability import metrics, names, tracing
from stt_platform.observability.prometheus import METRICS_PATH
from stt_platform.providers.registry import ProviderRegistry

PACKAGE_DIR = Path(names.__file__).parent
DASHBOARD = PACKAGE_DIR / "dashboards" / "stt-overview.json"
ALERTS = PACKAGE_DIR / "alerts" / "stt-alerts.yaml"

_METRIC_RE = re.compile(r"\b(stt_[a-z0-9_]+)\b")
_HISTOGRAM_SUFFIXES = ("_bucket", "_count", "_sum")


def _base_name(metric: str) -> str:
    for suffix in _HISTOGRAM_SUFFIXES:
        if metric.endswith(suffix):
            return metric[: -len(suffix)]
    return metric


def _registry_names() -> set[str]:
    return set(names.ALL)


def _metric_names_in(expressions: list[str]) -> set[str]:
    found: set[str] = set()
    for expression in expressions:
        for match in _METRIC_RE.findall(expression):
            found.add(_base_name(match))
    return found


def _settings(**overrides) -> Settings:
    base: dict[str, object] = dict(
        otel_enabled=True,
        otel_exporter="none",
        database_url="postgresql+psycopg://stt:stt@127.0.0.1:25432/stt",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean_process_state():
    """Apps created here install a process-wide provider; undo that."""
    metrics.shutdown_metrics()
    tracing.shutdown_tracing()
    yield
    metrics.shutdown_metrics()
    tracing.shutdown_tracing()


# --------------------------------------------------------------- endpoint #


def test_metrics_endpoint_serves_recorded_metrics_when_enabled():
    app = create_app(
        _settings(prometheus_enabled=True), registry=ProviderRegistry()
    )
    client = TestClient(app)

    client.get("/v1/providers")  # produce one datapoint
    response = client.get(METRICS_PATH)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "stt_api_requests_total" in response.text


def test_metrics_endpoint_is_absent_when_disabled():
    app = create_app(
        _settings(prometheus_enabled=False), registry=ProviderRegistry()
    )
    client = TestClient(app)

    assert client.get(METRICS_PATH).status_code == 404


# -------------------------------------------------------------- dashboard #


def test_dashboard_parses_and_references_only_registry_metrics():
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))

    assert dashboard["title"]
    panels = [p for p in dashboard.get("panels", []) if p.get("targets")]
    assert panels, "the dashboard must have panels with queries"

    expressions = [
        target["expr"] for panel in panels for target in panel["targets"]
    ]
    unknown = _metric_names_in(expressions) - _registry_names()
    assert not unknown, f"dashboard references unknown metrics: {sorted(unknown)}"
    for panel in panels:
        assert panel.get("title")
        for target in panel["targets"]:
            assert target.get("legendFormat") is not None


def test_dashboard_covers_the_spec_metrics():
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    expressions = [
        target["expr"]
        for panel in dashboard.get("panels", [])
        for target in panel.get("targets", [])
    ]
    used = _metric_names_in(expressions)

    required = {
        names.API_REQUEST_DURATION,
        names.API_ERRORS,
        names.JOB_QUEUE_WAIT,
        names.JOB_END_TO_END,
        names.JOB_PROCESSING,
        names.JOB_REAL_TIME_FACTOR,
        names.PROVIDER_DURATION,
        names.PROVIDER_ERRORS,
        names.PROVIDER_THROTTLED,
        names.QUEUE_DEPTH,
        names.PROCESS_CPU_PERCENT,
        names.PROCESS_MEMORY_BYTES,
    }
    missing = required - used
    assert not missing, f"dashboard does not chart: {sorted(missing)}"


# ----------------------------------------------------------------- alerts #


def test_alert_rules_parse_and_reference_only_registry_metrics():
    rules = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))

    groups = rules["groups"]
    assert groups
    expressions = [
        rule["expr"] for group in groups for rule in group["rules"]
    ]
    unknown = _metric_names_in(expressions) - _registry_names()
    assert not unknown, f"alerts reference unknown metrics: {sorted(unknown)}"

    for group in groups:
        for rule in group["rules"]:
            assert rule["alert"]
            assert rule["expr"]
            assert rule.get("for"), f"{rule['alert']} must have a 'for' window"
            assert rule["labels"]["severity"] in {"warning", "critical"}
            assert rule["annotations"]["summary"]


def test_alerts_cover_the_failure_modes_that_matter():
    rules = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    expressions = [
        rule["expr"] for group in rules["groups"] for rule in group["rules"]
    ]
    used = _metric_names_in(expressions)

    required = {
        names.API_ERRORS,
        names.PROVIDER_ERRORS,
        names.JOB_END_TO_END,
        names.QUEUE_DEPTH,
        names.STORAGE_ERRORS,
    }
    missing = required - used
    assert not missing, f"no alert uses: {sorted(missing)}"


def test_every_registered_metric_is_used_somewhere():
    """A metric nobody charts or alerts on is either dead or a gap."""
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    rules = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    expressions = [
        target["expr"]
        for panel in dashboard.get("panels", [])
        for target in panel.get("targets", [])
    ] + [rule["expr"] for group in rules["groups"] for rule in group["rules"]]

    used = _metric_names_in(expressions)
    unused = _registry_names() - used
    assert not unused, f"registered but never used: {sorted(unused)}"


@pytest.mark.parametrize("attr", ["ALL"])
def test_registry_is_a_flat_list_of_unique_names(attr):
    values = getattr(names, attr)

    assert len(values) == len(set(values))
    assert all(isinstance(value, str) for value in values)
    assert all(value.startswith("stt_") for value in values)
