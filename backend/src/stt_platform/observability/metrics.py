"""Metrics: one meter provider, off by default, instruments in one place.

Recording helpers (``record_api_request``, ``record_provider_call``, …) are
safe to call when metrics are disabled: they write to the API's no-op
instruments, so no call site needs a branch.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass

from opentelemetry import metrics as metrics_api
from opentelemetry.metrics import Counter, Histogram, Meter, UpDownCounter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader

from stt_platform.core.config import Settings
from stt_platform.observability import names


@dataclass
class _State:
    """Process-wide singletons (no module-level ``global`` juggling)."""

    provider: MeterProvider | None = None
    instruments: Instruments | None = None
    last_depth: int = 0


_STATE = _State()


@dataclass(frozen=True)
class Instruments:
    api_requests: Counter
    api_request_duration: Histogram
    api_errors: Counter
    upload_bytes: Histogram
    job_queue_wait: Histogram
    job_end_to_end: Histogram
    job_processing: Histogram
    job_real_time_factor: Histogram
    job_attempts: Counter
    job_retries: Counter
    job_fallbacks: Counter
    provider_calls: Counter
    provider_duration: Histogram
    provider_errors: Counter
    provider_throttled: Counter
    storage_errors: Counter
    queue_depth: UpDownCounter

    @classmethod
    def from_meter(cls, meter: Meter) -> Instruments:
        return cls(
            api_requests=meter.create_counter(
                names.API_REQUESTS,
                unit="1",
                description="HTTP requests handled by the API",
            ),
            api_request_duration=meter.create_histogram(
                names.API_REQUEST_DURATION,
                unit="s",
                description="Wall-clock duration of API requests",
            ),
            api_errors=meter.create_counter(
                names.API_ERRORS,
                unit="1",
                description="HTTP responses with status >= 400",
            ),
            upload_bytes=meter.create_histogram(
                names.UPLOAD_BYTES,
                unit="By",
                description="Size of uploaded audio",
            ),
            job_queue_wait=meter.create_histogram(
                names.JOB_QUEUE_WAIT,
                unit="s",
                description="Accepted -> processing start",
            ),
            job_end_to_end=meter.create_histogram(
                names.JOB_END_TO_END,
                unit="s",
                description="Accepted -> terminal status",
            ),
            job_processing=meter.create_histogram(
                names.JOB_PROCESSING,
                unit="s",
                description="Provider processing duration per attempt",
            ),
            job_real_time_factor=meter.create_histogram(
                names.JOB_REAL_TIME_FACTOR,
                unit="1",
                description="Processing seconds per audio second",
            ),
            job_attempts=meter.create_counter(
                names.JOB_ATTEMPTS,
                unit="1",
                description="Provider attempts started",
            ),
            job_retries=meter.create_counter(
                names.JOB_RETRIES,
                unit="1",
                description="Retries scheduled after a retryable failure",
            ),
            job_fallbacks=meter.create_counter(
                names.JOB_FALLBACKS,
                unit="1",
                description="Attempts served by a different provider than requested",
            ),
            provider_calls=meter.create_counter(
                names.PROVIDER_CALLS,
                unit="1",
                description="Provider operations by outcome",
            ),
            provider_duration=meter.create_histogram(
                names.PROVIDER_DURATION,
                unit="s",
                description="Provider call latency",
            ),
            provider_errors=meter.create_counter(
                names.PROVIDER_ERRORS,
                unit="1",
                description="Provider call failures by error code",
            ),
            provider_throttled=meter.create_counter(
                names.PROVIDER_THROTTLED,
                unit="1",
                description="Provider rate-limit (429) responses",
            ),
            storage_errors=meter.create_counter(
                names.STORAGE_ERRORS,
                unit="1",
                description="Object-storage failures",
            ),
            queue_depth=meter.create_up_down_counter(
                names.QUEUE_DEPTH,
                unit="1",
                description="Pending outbox rows (dispatch backlog)",
            ),
        )


def setup_metrics(
    settings: Settings, *, reader: MetricReader | None = None
) -> MeterProvider | None:
    """Install the process meter provider; idempotent."""
    if not settings.otel_enabled:
        return None
    if _STATE.provider is not None:
        return _STATE.provider

    provider = MeterProvider(metric_readers=[reader] if reader else [])
    metrics_api.set_meter_provider(provider)
    _STATE.provider = provider
    _STATE.instruments = Instruments.from_meter(
        provider.get_meter("stt_platform")
    )
    return provider


def shutdown_metrics() -> None:
    provider, _STATE.provider = _STATE.provider, None
    _STATE.instruments = None
    if provider is None:
        return
    with contextlib.suppress(Exception):  # shutdown must never raise
        provider.shutdown()


def active_meter_provider() -> MeterProvider | None:
    return _STATE.provider


def instruments() -> Instruments:
    """Live instruments, or no-op ones when metrics are disabled."""
    if _STATE.instruments is None:
        _STATE.instruments = Instruments.from_meter(
            metrics_api.get_meter("stt_platform")
        )
    return _STATE.instruments


# --------------------------------------------------------------------- #
# recording helpers
# --------------------------------------------------------------------- #


def record_api_request(
    *, route: str, method: str, status: int, duration_s: float
) -> None:
    attributes = {
        "http.route": route,
        "http.request.method": method,
        "http.response.status_code": status,
    }
    inst = instruments()
    inst.api_requests.add(1, attributes)
    inst.api_request_duration.record(duration_s, attributes)
    if status >= 400:
        inst.api_errors.add(1, attributes)


def record_upload_bytes(size_bytes: int, *, content_type: str | None = None) -> None:
    instruments().upload_bytes.record(
        size_bytes, {"content_type": content_type or "unknown"}
    )


def record_job_accepted(*, provider: str) -> None:
    instruments().job_attempts.add(1, {"provider": provider, "phase": "accepted"})


def record_attempt_started(*, provider: str, attempt: int) -> None:
    instruments().job_attempts.add(
        1, {"provider": provider, "attempt": str(attempt)}
    )


def record_retry(*, provider: str, error_code: str) -> None:
    instruments().job_retries.add(
        1, {"provider": provider, "error_code": error_code}
    )


def record_fallback(*, requested: str, actual: str) -> None:
    instruments().job_fallbacks.add(
        1, {"provider.requested": requested, "provider.actual": actual}
    )


def record_processing(
    *, provider: str, duration_s: float, audio_duration_s: float | None
) -> None:
    inst = instruments()
    inst.job_processing.record(duration_s, {"provider": provider})
    if audio_duration_s and audio_duration_s > 0:
        inst.job_real_time_factor.record(
            duration_s / audio_duration_s, {"provider": provider}
        )


def record_queue_wait(*, provider: str, seconds: float) -> None:
    instruments().job_queue_wait.record(seconds, {"provider": provider})


def record_end_to_end(*, provider: str, status: str, seconds: float) -> None:
    instruments().job_end_to_end.record(
        seconds, {"provider": provider, "status": status}
    )


def record_provider_call(
    *, provider: str, operation: str, outcome: str, duration_s: float
) -> None:
    inst = instruments()
    attributes = {
        "provider": provider,
        "operation": operation,
        "outcome": outcome,
    }
    inst.provider_calls.add(1, attributes)
    inst.provider_duration.record(duration_s, attributes)
    if outcome != "ok":
        inst.provider_errors.add(1, attributes)
    if outcome == "throttled":
        inst.provider_throttled.add(1, attributes)


def record_storage_error(*, operation: str, error_type: str) -> None:
    instruments().storage_errors.add(
        1, {"operation": operation, "error_type": error_type}
    )


def set_queue_depth(pending: int) -> None:
    """Gauge-style backlog: adjust by the delta since the last reading."""
    instruments().queue_depth.add(pending - _STATE.last_depth)
    _STATE.last_depth = pending
