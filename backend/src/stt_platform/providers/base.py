"""Stable internal provider contract.

Every provider — local GPU engines or managed APIs — implements
``ProviderProtocol``. Adapters translate native requests, responses,
exceptions, and timestamps into these canonical types; provider SDK
objects never escape the adapter (spec §6).
"""
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

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


class RemoteJobRef(BaseModel):
    """Opaque reference to a provider-side asynchronous job."""

    provider_job_id: str
    raw: dict | None = None


class RemoteJobState(BaseModel):
    """One poll result for a provider-side job.

    ``state`` is the provider's own verdict mapped to three values:
    ``pending`` (keep polling), ``completed`` (transcript attached),
    ``failed`` (terminal on the provider side; ``retryable`` carries the
    provider's guidance, never our guess).
    """

    state: Literal["pending", "completed", "failed"]
    transcript: ProviderTranscript | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    raw: dict | None = None


@runtime_checkable
class ProviderProtocol(Protocol):
    id: str
    name: str
    is_local: bool

    def capabilities(self) -> ProviderCapabilities: ...

    def health(self) -> ProviderHealth: ...

    def transcribe(self, request: TranscriptionRequest) -> ProviderTranscript: ...


@runtime_checkable
class AsyncProviderProtocol(Protocol):
    """Additive contract for providers with asynchronous job APIs.

    Providers may implement both protocols (a sync endpoint plus a batch
    one); the workflow uses the async path whenever a provider implements
    this protocol. ``cancel`` returning ``False`` means the provider has
    no cancellation API — that is reported, never papered over.
    """

    def submit(self, request: TranscriptionRequest) -> RemoteJobRef: ...

    def poll(self, ref: RemoteJobRef) -> RemoteJobState: ...

    def cancel(self, ref: RemoteJobRef) -> bool: ...
