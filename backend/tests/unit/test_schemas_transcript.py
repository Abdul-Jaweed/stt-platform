import pytest
from pydantic import ValidationError

from stt_platform.schemas.transcript import (
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)


def make_segment(start=0.0, end=1.0, text="hello", words=None):
    return TranscriptSegment(start=start, end=end, text=text, words=words or [])


def make_result(**overrides):
    base = dict(
        job_id="job-1",
        text="hello world",
        language="en",
        segments=[make_segment()],
        timestamp_granularity="segment",
        audio_duration_s=2.0,
        processing_duration_s=0.5,
        provider="fake",
        model="fake-model",
    )
    base.update(overrides)
    return TranscriptResult(**base)


def test_valid_result_roundtrips_through_json():
    result = make_result()
    assert TranscriptResult.model_validate_json(result.model_dump_json()) == result


def test_granularity_segment_forbids_word_timings():
    seg = make_segment(
        words=[TranscriptWord(start=0.0, end=0.5, text="hello", confidence=0.9)]
    )
    with pytest.raises(ValidationError):
        make_result(segments=[seg], timestamp_granularity="segment")


def test_granularity_word_requires_word_timings():
    with pytest.raises(ValidationError):
        make_result(segments=[make_segment()], timestamp_granularity="word")


def test_word_granularity_accepts_word_timings():
    seg = make_segment(
        words=[TranscriptWord(start=0.0, end=0.5, text="hello", confidence=0.9)]
    )
    result = make_result(segments=[seg], timestamp_granularity="word")
    assert result.timestamp_granularity == "word"
    assert result.segments[0].words[0].confidence == pytest.approx(0.9)


def test_segment_end_must_exceed_start():
    with pytest.raises(ValidationError):
        TranscriptSegment(start=1.0, end=0.5, text="x")


def test_segments_must_be_ordered_by_start():
    with pytest.raises(ValidationError):
        make_result(
            segments=[
                make_segment(start=1.0, end=1.5),
                make_segment(start=0.5, end=3.0),
            ]
        )


def test_segment_end_within_duration_tolerance():
    # within +0.5 s tolerance: accepted
    make_result(segments=[make_segment(start=1.0, end=10.4)], audio_duration_s=10.0)
    # beyond tolerance: rejected
    with pytest.raises(ValidationError):
        make_result(
            segments=[make_segment(start=1.0, end=10.6)], audio_duration_s=10.0
        )


def test_negative_start_rejected():
    with pytest.raises(ValidationError):
        TranscriptSegment(start=-0.1, end=1.0, text="x")


def test_word_confidence_bounds():
    with pytest.raises(ValidationError):
        TranscriptWord(start=0.0, end=0.5, text="x", confidence=1.5)


def test_empty_segments_allowed_for_silence():
    result = make_result(text="", segments=[])
    assert result.segments == []
