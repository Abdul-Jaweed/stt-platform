"""Runtime sampler: CPU, RSS, GPU, and the outbox backlog gauge.

The sampler is a daemon thread in the worker: a gauge is a *reading*, so it
must be refreshed on a timer rather than written at a call site. NVML is
imported lazily — a machine without an NVIDIA driver records CPU/RSS and
warns once, never crashes.
"""
from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable

import psutil

from stt_platform.observability import metrics

log = logging.getLogger(__name__)

_gpu_warn_lock = threading.Lock()


class _GpuWarning:
    issued: bool = False


def _load_nvml():
    """Import pynvml lazily; ``None`` when the driver or package is absent."""
    try:
        import pynvml
    except Exception:
        return None
    return pynvml


def _warn_gpu_once(reason: str) -> None:
    with _gpu_warn_lock:
        if _GpuWarning.issued:
            return
        _GpuWarning.issued = True
    log.warning("GPU metrics unavailable, continuing without them: %s", reason)


def _sample_gpu() -> dict[str, float]:
    nvml = _load_nvml()
    if nvml is None:
        _warn_gpu_once("pynvml not importable")
        return {}
    try:
        nvml.nvmlInit()
    except Exception as exc:
        _warn_gpu_once(f"nvmlInit failed: {exc!r}")
        return {}
    try:
        if nvml.nvmlDeviceGetCount() < 1:
            return {}
        handle = nvml.nvmlDeviceGetHandleByIndex(0)
        utilization = nvml.nvmlDeviceGetUtilizationRates(handle)
        memory = nvml.nvmlDeviceGetMemoryInfo(handle)
        return {
            "gpu_utilization_percent": float(utilization.gpu),
            "gpu_memory_bytes": float(memory.used),
        }
    except Exception as exc:
        _warn_gpu_once(f"nvml query failed: {exc!r}")
        return {}
    finally:
        with contextlib.suppress(Exception):  # shutdown is best-effort
            nvml.nvmlShutdown()


def sample_runtime() -> dict[str, float]:
    """One reading of this process (and the first GPU, when available)."""
    process = psutil.Process()
    values = {
        "cpu_percent": float(process.cpu_percent(interval=None)),
        "memory_bytes": float(process.memory_info().rss),
    }
    values.update(_sample_gpu())
    return values


class RuntimeSampler:
    """Daemon thread publishing runtime gauges on an interval."""

    def __init__(
        self,
        *,
        interval_s: float = 15.0,
        queue_depth_fn: Callable[[], int] | None = None,
        sample_fn: Callable[[], dict[str, float]] = sample_runtime,
    ) -> None:
        self._interval_s = interval_s
        self._queue_depth_fn = queue_depth_fn
        self._sample_fn = sample_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="stt-runtime-sampler", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)

    def sample_once(self) -> None:
        """Publish one reading; failures are logged, never raised."""
        values: dict[str, float] = {}
        try:
            values.update(self._sample_fn())
        except Exception:
            log.debug("runtime sample failed", exc_info=True)
        if self._queue_depth_fn is not None:
            try:
                values["queue_depth"] = float(self._queue_depth_fn())
            except Exception:
                log.debug("queue depth query failed", exc_info=True)
        if values:
            metrics.set_runtime_snapshot(values)

    def _run(self) -> None:
        # First read of cpu_percent is always 0.0 (psutil needs a baseline).
        self.sample_once()
        while not self._stop.wait(self._interval_s):
            self.sample_once()
        self.sample_once()


def start_runtime_sampler(
    *, interval_s: float, queue_depth_fn: Callable[[], int] | None = None
) -> RuntimeSampler | None:
    """Start the sampler when metrics are enabled; ``None`` otherwise."""
    if metrics.active_meter_provider() is None:
        return None
    sampler = RuntimeSampler(
        interval_s=interval_s, queue_depth_fn=queue_depth_fn
    )
    sampler.start()
    return sampler
