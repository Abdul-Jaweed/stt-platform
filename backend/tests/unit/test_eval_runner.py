"""The evaluation runner: per-clip records, aggregates, honest failure handling.

The transcribe callable is injected, so these tests exercise measurement
logic with deterministic transcripts; the platform path (real jobs, real
timings) is covered by the integration tests.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_platform.evaluation.datasets import Clip, Dataset
from stt_platform.evaluation.runner import (
    ClipOutcome,
    EvaluationError,
    run_evaluation,
)


def _clip(clip_id: str, text: str, **overrides: object) -> Clip:
    base: dict = {
        "clip_id": clip_id,
        "audio": Path(f"/tmp/{clip_id}.wav"),
        "reference_text": text,
        "duration_s": 4.0,
        "language": "en",
        "domain": "technical",
        "accent": "synthetic-us",
        "noise": "none",
        "reference_source": "synthetic-tts",
        "reference_segments": (
            SimpleNamespace(start=0.0, end=4.0, text=text),
        ),
        "sha256": "deadbeef",
    }
    base.update(overrides)
    return Clip(**base)  # type: ignore[arg-type]


def _dataset(*clips: Clip) -> Dataset:
    return Dataset(
        dataset_id="test-set",
        version="1.0.0",
        language="en",
        audio_root=Path("/tmp"),
        clips=clips,
        missing_audio=(),
        path=Path("/tmp/manifest.json"),
    )


def _outcome(text: str, **overrides: object) -> ClipOutcome:
    base: dict = {
        "text": text,
        "segments": [SimpleNamespace(start=0.0, end=4.0, words=[])],
        "granularity": "segment",
        "timings": {
            "queue_wait_s": 0.5,
            "processing_s": 1.0,
            "end_to_end_s": 1.6,
            "real_time_factor": 0.25,
        },
        "attempts": 1,
        "retries": 0,
        "provider": "fake",
        "model": "fake-model-v1",
    }
    base.update(overrides)
    return ClipOutcome(**base)  # type: ignore[arg-type]


def test_perfect_transcripts_score_zero_and_record_timings():
    dataset = _dataset(_clip("clip-a", "hello world"), _clip("clip-b", "goodbye moon"))
    result = run_evaluation(
        dataset=dataset,
        transcribe=lambda clip: _outcome(clip.reference_text or ""),
        provider="fake",
        model="fake-model-v1",
        decoding={"language": "en"},
    )

    assert [clip.clip_id for clip in result.clips] == ["clip-a", "clip-b"]
    assert result.aggregate["wer_micro"] == 0.0
    assert result.aggregate["clips_scored"] == 2
    assert result.aggregate["failures"] == 0
    first = result.clips[0]
    assert first.timings["processing_s"] == 1.0
    assert first.model == "fake-model-v1"
    assert first.audio_sha256 == "deadbeef"
    assert result.decoding == {"language": "en"}


def test_aggregate_reports_accuracy_and_timestamps():
    dataset = _dataset(_clip("clip-a", "the cat sat"), _clip("clip-b", "hello world"))
    transcripts = {"clip-a": "the cat sit", "clip-b": "hello world"}
    result = run_evaluation(
        dataset=dataset,
        transcribe=lambda clip: _outcome(transcripts[clip.clip_id]),
        provider="fake",
        model="m",
    )

    # clip-a: 1 error over 3 words; clip-b: clean over 2 words → 1/5 micro
    assert result.aggregate["wer_micro"] == pytest.approx(0.2)
    assert result.aggregate["clips_scored"] == 2
    assert result.aggregate["segment_availability_mean"] == 1.0
    assert result.aggregate["word_availability_mean"] is None  # no words at all
    assert result.aggregate["rtf_mean"] == pytest.approx(0.25)
    assert result.aggregate["boundary_error_mean_s"] == pytest.approx(0.0)


def test_a_failing_clip_is_recorded_and_does_not_abort_the_run():
    dataset = _dataset(_clip("clip-a", "hello world"), _clip("clip-b", "goodbye moon"))

    def transcribe(clip: Clip) -> ClipOutcome:
        if clip.clip_id == "clip-b":
            raise RuntimeError("provider exploded")
        return _outcome(clip.reference_text or "")

    result = run_evaluation(dataset=dataset, transcribe=transcribe, provider="fake")

    failed = next(clip for clip in result.clips if clip.clip_id == "clip-b")
    assert failed.status == "failed"
    assert failed.error and "provider exploded" in failed.error
    assert result.aggregate["failures"] == 1
    assert result.aggregate["failure_rate"] == pytest.approx(0.5)
    assert result.aggregate["clips_scored"] == 1


def test_unscored_clips_are_never_sent_to_a_provider():
    scored = _clip("clip-a", "hello world")
    pending = _clip(
        "clip-b",
        "",
        reference_text=None,
        reference_segments=(),
        reference_source="pending-human-verification",
    )
    seen: list[str] = []

    def transcribe(clip: Clip) -> ClipOutcome:
        seen.append(clip.clip_id)
        return _outcome("hello world")

    result = run_evaluation(
        dataset=_dataset(scored, pending), transcribe=transcribe, provider="fake"
    )

    assert seen == ["clip-a"]
    assert all(clip.clip_id != "clip-b" for clip in result.clips)
    assert result.aggregate["clips_total"] == 1


def test_missing_audio_for_a_scored_clip_fails_loudly():
    dataset = Dataset(
        dataset_id="test-set",
        version="1.0.0",
        language="en",
        audio_root=Path("/tmp"),
        clips=(_clip("clip-a", "hello world"),),
        missing_audio=("clip-a",),
        path=Path("/tmp/manifest.json"),
    )
    with pytest.raises(EvaluationError, match="missing audio"):
        run_evaluation(dataset=dataset, transcribe=lambda clip: _outcome("x"), provider="fake")


def test_one_recording_is_not_enough_to_rank_a_provider():
    result = run_evaluation(
        dataset=_dataset(_clip("clip-a", "hello world")),
        transcribe=lambda clip: _outcome("hello world"),
        provider="fake",
    )
    assert result.insufficient_evidence is True

    two = run_evaluation(
        dataset=_dataset(_clip("clip-a", "hello world"), _clip("clip-b", "hi")),
        transcribe=lambda clip: _outcome("hello world"),
        provider="fake",
    )
    assert two.insufficient_evidence is False


def test_accuracy_is_broken_down_by_domain_and_noise():
    dataset = _dataset(
        _clip("clip-a", "hello world", domain="technical", noise="none"),
        _clip("clip-b", "hello world", domain="conversational", noise="white-amix-0.08"),
    )

    def transcribe(clip: Clip) -> ClipOutcome:
        text = clip.reference_text or ""
        return _outcome(text if clip.noise == "none" else "hello there")

    result = run_evaluation(dataset=dataset, transcribe=transcribe, provider="fake")

    by_domain = result.by_group["domain"]
    assert by_domain["technical"]["wer_micro"] == 0.0
    assert by_domain["conversational"]["wer_micro"] == pytest.approx(0.5)
    assert result.by_group["noise"]["white-amix-0.08"]["clips"] == 1
    assert result.by_group["noise"]["none"]["clips"] == 1


def test_result_is_recordable():
    result = run_evaluation(
        dataset=_dataset(_clip("clip-a", "hello world"), _clip("clip-b", "hi there")),
        transcribe=lambda clip: _outcome(clip.reference_text or ""),
        provider="fake",
        model="m",
        decoding={"language": "en"},
    )
    payload = result.as_dict()

    assert payload["dataset"]["dataset_id"] == "test-set"
    assert payload["dataset"]["version"] == "1.0.0"
    assert payload["provider"] == "fake"
    assert payload["model"] == "m"
    assert payload["normalization"]["name"] == "stt-normalize-v1"
    assert payload["aggregate"]["clips_scored"] == 2
    assert payload["clips"][0]["accuracy"]["wer"] == 0.0
    assert payload["insufficient_evidence"] is False
    assert payload["duration_s"] >= 0


def test_each_scored_clip_carries_its_reference_and_alignment():
    """The artifact must let a reader (or the UI) see *what was compared*:
    the reference, the transcript, and the per-token ops behind the WER."""
    dataset = _dataset(_clip("c1", "the quick brown fox"))
    result = run_evaluation(
        dataset=dataset,
        provider="fake",
        model=None,
        transcribe=lambda clip: ClipOutcome(
            text="the slow brown",
            segments=(),
            granularity=None,
            timings={},
            attempts=1,
            retries=0,
            provider="fake",
        ),
    )

    clip = result.as_dict()["clips"][0]
    assert clip["reference_text"] == "the quick brown fox"
    alignment = clip["alignment"]
    assert alignment["code"] == "MSMD"
    assert alignment["reference_tokens"] == ["the", "quick", "brown", "fox"]
    assert alignment["hypothesis_tokens"] == ["the", "slow", "brown"]
    assert clip["accuracy"]["word_errors"]["substitutions"] == 1
    assert clip["accuracy"]["word_errors"]["deletions"] == 1


def test_a_failed_clip_carries_no_alignment_at_all():
    """A clip nobody transcribed has nothing to align: ``None``, not an empty
    diff that would read like an agreement."""
    dataset = _dataset(_clip("c1", "the quick brown fox"))

    def boom(clip):
        raise RuntimeError("provider exploded")

    result = run_evaluation(
        dataset=dataset, provider="fake", model=None, transcribe=boom
    )

    clip = result.as_dict()["clips"][0]
    assert clip["status"] == "failed"
    assert clip["alignment"] is None
    assert clip["reference_text"] == "the quick brown fox"


def test_the_aggregate_metrics_are_explained_in_the_same_place():
    from stt_platform.evaluation.runner import RUNNER_METRIC_SPECS

    assert RUNNER_METRIC_SPECS, "aggregate metrics are not described"
    for name in ("failure_rate", "rtf_mean", "coverage_mean"):
        spec = RUNNER_METRIC_SPECS[name]
        assert spec.formula.strip() and spec.limitations.strip()
        assert callable(spec.compute)
