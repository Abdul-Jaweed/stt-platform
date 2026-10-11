"""Runtime gauges: CPU/RSS from psutil, GPU when NVML is there, backlog."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from stt_platform.core.config import Settings
from stt_platform.observability import metrics, runtime


def _settings() -> Settings:
    return Settings(
        otel_enabled=True,
        otel_exporter="none",
        database_url="postgresql+psycopg://stt:stt@127.0.0.1:25432/stt",
    )


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


def _value(reader: InMemoryMetricReader, name: str) -> float | None:
    points = _points(reader, name)
    return points[0].value if points else None


@pytest.fixture()
def reader():
    metric_reader = InMemoryMetricReader()
    metrics.shutdown_metrics()  # setup_metrics is idempotent per process
    metrics.reset_runtime_snapshot()
    metrics.setup_metrics(_settings(), reader=metric_reader)
    yield metric_reader
    metrics.shutdown_metrics()
    metrics.reset_runtime_snapshot()


class _FakeNvml:
    def __init__(self, *, gpu: int = 55, used: int = 2048, count: int = 1):
        self.gpu = gpu
        self.used = used
        self.count = count

    def nvmlInit(self):
        return None

    def nvmlShutdown(self):
        return None

    def nvmlDeviceGetCount(self):
        return self.count

    def nvmlDeviceGetHandleByIndex(self, index):
        return "handle-0"

    def nvmlDeviceGetUtilizationRates(self, handle):
        return SimpleNamespace(gpu=self.gpu, memory=10)

    def nvmlDeviceGetMemoryInfo(self, handle):
        return SimpleNamespace(used=self.used, total=8192)


def test_sample_runtime_reads_cpu_and_memory(monkeypatch):
    monkeypatch.setattr(runtime, "_load_nvml", lambda: None)

    values = runtime.sample_runtime()

    assert values["memory_bytes"] > 0
    assert values["cpu_percent"] >= 0.0
    assert "gpu_utilization_percent" not in values


def test_gpu_values_are_sampled_when_nvml_is_available(monkeypatch):
    monkeypatch.setattr(runtime, "_load_nvml", lambda: _FakeNvml(gpu=77, used=4096))

    values = runtime.sample_runtime()

    assert values["gpu_utilization_percent"] == 77.0
    assert values["gpu_memory_bytes"] == 4096.0


def test_missing_nvml_warns_once_and_never_raises(monkeypatch, caplog):
    monkeypatch.setattr(runtime, "_load_nvml", lambda: None)
    monkeypatch.setattr(runtime._GpuWarning, "issued", False)

    with caplog.at_level(logging.WARNING, logger="stt_platform.observability.runtime"):
        first = runtime.sample_runtime()
        second = runtime.sample_runtime()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "GPU metrics unavailable" in warnings[0].getMessage()
    assert "gpu_utilization_percent" not in first
    assert "gpu_utilization_percent" not in second


def test_broken_nvml_call_is_contained(monkeypatch):
    class Exploding(_FakeNvml):
        def nvmlDeviceGetUtilizationRates(self, handle):
            raise RuntimeError("driver gone")

    monkeypatch.setattr(runtime, "_load_nvml", Exploding)
    monkeypatch.setattr(runtime._GpuWarning, "issued", False)

    values = runtime.sample_runtime()

    assert values["memory_bytes"] > 0
    assert "gpu_utilization_percent" not in values


def test_gauges_reflect_the_latest_snapshot(reader):
    assert _points(reader, "stt_process_cpu_percent") == []

    metrics.set_runtime_snapshot({"cpu_percent": 12.5, "memory_bytes": 4096})
    assert _value(reader, "stt_process_cpu_percent") == 12.5
    assert _value(reader, "stt_process_memory_bytes") == 4096.0

    # A later reading replaces the previous value (a gauge, not a counter).
    metrics.set_runtime_snapshot({"cpu_percent": 3.0})
    assert _value(reader, "stt_process_cpu_percent") == 3.0

    # Keys never sampled stay absent rather than reporting zero.
    assert _points(reader, "stt_gpu_utilization_percent") == []
    metrics.set_runtime_snapshot({"gpu_utilization_percent": 61.0})
    assert _value(reader, "stt_gpu_utilization_percent") == 61.0


def test_queue_depth_is_a_gauge_of_pending_rows(reader):
    metrics.set_queue_depth(7)

    assert _value(reader, "stt_queue_depth") == 7.0

    metrics.set_queue_depth(0)
    assert _value(reader, "stt_queue_depth") == 0.0


def test_sampler_publishes_readings_and_stops(reader):
    calls: list[str] = []

    def sample_fn() -> dict[str, float]:
        calls.append("sample")
        return {"cpu_percent": 5.0, "memory_bytes": 1024.0}

    sampler = runtime.RuntimeSampler(
        interval_s=0.05, queue_depth_fn=lambda: 3, sample_fn=sample_fn
    )
    sampler.start()
    sampler.start()  # idempotent
    try:
        assert sampler._thread is not None
        deadline = 2.0
        import time as _time

        start = _time.monotonic()
        while _value(reader, "stt_queue_depth") != 3.0 and _time.monotonic() - start < deadline:
            _time.sleep(0.02)
        assert _value(reader, "stt_queue_depth") == 3.0
        assert _value(reader, "stt_process_cpu_percent") == 5.0
    finally:
        sampler.stop()

    assert sampler._thread is None
    assert calls, "the sampler must have published at least one reading"


def test_sampler_survives_a_failing_sample(reader):
    def broken() -> dict[str, float]:
        raise RuntimeError("psutil exploded")

    sampler = runtime.RuntimeSampler(
        interval_s=0.05, queue_depth_fn=lambda: 1, sample_fn=broken
    )
    sampler.sample_once()  # must not raise

    assert _value(reader, "stt_queue_depth") == 1.0


def test_start_runtime_sampler_is_off_without_metrics():
    metrics.shutdown_metrics()

    assert runtime.start_runtime_sampler(interval_s=1.0) is None
