"""Canonical metric names.

One source of truth: the instruments in ``metrics.py`` are built from these
constants, and the Grafana dashboard and alert rules (T7) are validated
against them — a name that exists in only one place fails the tests.
"""
from __future__ import annotations

API_REQUESTS = "stt_api_requests_total"
API_REQUEST_DURATION = "stt_api_request_duration_seconds"
API_ERRORS = "stt_api_errors_total"

UPLOAD_BYTES = "stt_upload_bytes"

JOB_QUEUE_WAIT = "stt_job_queue_wait_seconds"
JOB_END_TO_END = "stt_job_end_to_end_seconds"
JOB_PROCESSING = "stt_job_processing_seconds"
JOB_REAL_TIME_FACTOR = "stt_job_real_time_factor"
JOB_ATTEMPTS = "stt_job_attempts_total"
JOB_RETRIES = "stt_job_retries_total"
JOB_FALLBACKS = "stt_job_fallbacks_total"

PROVIDER_CALLS = "stt_provider_calls_total"
PROVIDER_DURATION = "stt_provider_duration_seconds"
PROVIDER_ERRORS = "stt_provider_errors_total"
PROVIDER_THROTTLED = "stt_provider_throttled_total"

STORAGE_ERRORS = "stt_storage_errors_total"

QUEUE_DEPTH = "stt_queue_depth"
PROCESS_CPU_PERCENT = "stt_process_cpu_percent"
PROCESS_MEMORY_BYTES = "stt_process_memory_bytes"
GPU_UTILIZATION_PERCENT = "stt_gpu_utilization_percent"
GPU_MEMORY_BYTES = "stt_gpu_memory_bytes"

ALL: tuple[str, ...] = (
    API_REQUESTS,
    API_REQUEST_DURATION,
    API_ERRORS,
    UPLOAD_BYTES,
    JOB_QUEUE_WAIT,
    JOB_END_TO_END,
    JOB_PROCESSING,
    JOB_REAL_TIME_FACTOR,
    JOB_ATTEMPTS,
    JOB_RETRIES,
    JOB_FALLBACKS,
    PROVIDER_CALLS,
    PROVIDER_DURATION,
    PROVIDER_ERRORS,
    PROVIDER_THROTTLED,
    STORAGE_ERRORS,
    QUEUE_DEPTH,
    PROCESS_CPU_PERCENT,
    PROCESS_MEMORY_BYTES,
    GPU_UTILIZATION_PERCENT,
    GPU_MEMORY_BYTES,
)
