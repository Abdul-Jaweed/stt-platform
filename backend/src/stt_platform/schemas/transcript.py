"""Canonical transcript schema.

Fidelity rules (spec §7):
- segment-level and word-level timings are distinct; ``timestamp_granularity``
  must match what is actually present;
- all timestamps refer to the original audio timeline;
- nothing (confidence, language, diarization) is fabricated.
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1.0"

# Segment ends may exceed the probed duration by up to this many seconds
# (decoder/relabeling slop; well beyond any legitimate disagreement).
DURATION_TOLERANCE_S = 0.5


class TranscriptWord(BaseModel):
    start: float = Field(ge=0)
    end: float
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _check(self) -> "TranscriptWord":
        if self.end <= self.start:
            raise ValueError("word end must be greater than start")
        if not self.text.strip():
            raise ValueError("word text must not be empty")
        return self


class TranscriptSegment(BaseModel):
    start: float = Field(ge=0)
    end: float
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    words: list[TranscriptWord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "TranscriptSegment":
        if self.end <= self.start:
            raise ValueError("segment end must be greater than start")
        if not self.text.strip():
            raise ValueError("segment text must not be empty")
        return self


class TranscriptResult(BaseModel):
    job_id: str
    schema_version: str = SCHEMA_VERSION
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
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_fidelity(self) -> "TranscriptResult":
        starts = [s.start for s in self.segments]
        if starts != sorted(starts):
            raise ValueError("segments must be ordered by start time")

        has_words = any(s.words for s in self.segments)
        if self.timestamp_granularity == "segment" and has_words:
            raise ValueError(
                "word timings present but timestamp_granularity is 'segment'"
            )
        if self.timestamp_granularity == "word" and not has_words:
            raise ValueError("timestamp_granularity 'word' requires word timings")

        if self.segments:
            last_end = self.segments[-1].end
            if last_end > self.audio_duration_s + DURATION_TOLERANCE_S:
                raise ValueError(
                    "segment end exceeds audio duration beyond tolerance"
                )
        return self
