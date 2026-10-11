"""Canonical job records."""
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from stt_platform.domain.status import JobStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


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
    original_object_key: str | None = None
    idempotency_key: str | None = None
    request_fingerprint: str | None = None
    attempt_count: int = 0
    failure: JobFailure | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PresignRequest(BaseModel):
    """Ask for a slot to upload an original directly to object storage."""

    filename: str | None = None


class PresignedUpload(BaseModel):
    """A one-shot PUT target; the key is opaque and scope-bound."""

    object_key: str
    upload_url: str
    method: str = "PUT"
    expires_in_s: int
