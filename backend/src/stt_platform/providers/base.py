"""Stable internal provider contract.

Every provider — local GPU engines or managed APIs — implements
``ProviderProtocol``. Adapters translate native requests, responses,
exceptions, and timestamps into these canonical types; provider SDK
objects never escape the adapter (spec §6).
"""
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from stt_platform.schemas.providers import ProviderCapabilities, ProviderHealth
from stt_platform.schemas.transcript import TranscriptSegment


class TranscriptionRequest(BaseModel):
    audio_path: Path
    language: str | None = None
    options: dict = Field(default_factory=dict)


class ProviderTranscript(BaseModel):
    """Provider-normalized transcription output (pre-job canonicalization)."""

    text: str
    language: str | None = None
    language_probability: float | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
    words_available: bool = False
    model: str
    model_version: str | None = None
    audio_duration_s: float
    processing_duration_s: float
    raw: dict | None = None


@runtime_checkable
class ProviderProtocol(Protocol):
    id: str
    name: str
    is_local: bool

    def capabilities(self) -> ProviderCapabilities: ...

    def health(self) -> ProviderHealth: ...

    def transcribe(self, request: TranscriptionRequest) -> ProviderTranscript: ...
