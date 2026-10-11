"""Live preview schema.

A preview is *not* a transcript: nothing is stored, no job exists, and the
response says so (`is_final: false`) so no client can mistake it for the
durable artifact. It carries the same segment shape as the canonical
transcript so the UI can render it with the same components.
"""
from typing import Literal

from pydantic import BaseModel, Field

from stt_platform.schemas.transcript import TranscriptSegment


class LivePreviewResponse(BaseModel):
    text: str
    language: str | None = None
    language_probability: float | None = Field(default=None, ge=0, le=1)
    segments: list[TranscriptSegment] = Field(default_factory=list)
    timestamp_granularity: Literal["segment", "word"] = "segment"
    audio_duration_s: float = Field(gt=0)
    processing_duration_s: float = Field(ge=0)
    provider: str
    model: str
    model_version: str | None = None
    #: Always false — a preview is never the finished transcript.
    is_final: Literal[False] = False
