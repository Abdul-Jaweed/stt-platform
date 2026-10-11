"""Canonical job records."""
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from stt_platform.domain.status import JobStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AudioMetadata(BaseModel):
    format_name: str
    codec: str | None = None
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    duration_s: float = Field(gt=0)
    size_bytes: int = Field(ge=0)


class JobFailure(BaseModel):
    code: str
    message: str
    retryable: bool
    provider_code: str | None = None


class JobRecord(BaseModel):
    id: str
    scope: str = "local"
    status: JobStatus
    requested_provider: str | None = None
    actual_provider: str | None = None
    model: str | None = None
    model_version: str | None = None
    language_requested: str | None = None
    language_detected: str | None = None
    audio: AudioMetadata | None = None
    audio_path: str | None = None
    idempotency_key: str | None = None
    request_fingerprint: str | None = None
    attempt_count: int = 0
    failure: JobFailure | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
