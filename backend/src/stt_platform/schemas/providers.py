"""Provider capability and health schemas.

``capability_sources`` records, per capability, whether it is merely
*documented* by the vendor or *verified* by our integration tests —
never conflate the two (spec §6).
"""
from typing import Literal

from pydantic import BaseModel, Field

CapabilitySource = Literal["documented", "verified"]


class ProviderCapabilities(BaseModel):
    languages: list[str] = Field(default_factory=list)
    language_detection: bool = False
    segment_timestamps: bool = True
    word_timestamps: bool = False
    diarization: bool = False
    streaming: bool = False
    batch: bool = True
    async_processing: bool = False
    confidence_scores: bool = False
    max_file_bytes: int | None = None
    max_duration_s: float | None = None
    formats: list[str] = Field(default_factory=list)
    capability_sources: dict[str, CapabilitySource] = Field(default_factory=dict)


class ProviderHealth(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    detail: str | None = None


class ProviderInfo(BaseModel):
    id: str
    name: str
    is_local: bool
    capabilities: ProviderCapabilities
    health: ProviderHealth
