"""Dataset manifests: stable ids, honest provenance, no invented references."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stt_platform.evaluation.datasets import DatasetError, load_dataset


def _manifest(tmp_path: Path, clips: list[dict]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "dataset_id": "test-set",
                "version": "1.0.0",
                "language": "en",
                "audio_root": "audio",
                "clips": clips,
            }
        ),
        encoding="utf-8",
    )
    return path


def _clip(clip_id: str, **overrides: object) -> dict:
    base: dict = {
        "clip_id": clip_id,
        "audio": f"{clip_id}.wav",
        "reference_text": "hello world",
        "duration_s": 1.0,
        "language": "en",
        "domain": "technical",
        "accent": "synthetic-us",
        "noise": "none",
        "reference_source": "synthetic-tts",
        "reference_segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}],
    }
    base.update(overrides)
    return base


def _load(tmp_path: Path, clips: list[dict], **kwargs: object):
    """Load a tmp manifest with its audio root pinned to tmp_path."""
    return load_dataset(
        _manifest(tmp_path, clips), audio_root=tmp_path / "audio", **kwargs
    )


def _write_audio(tmp_path: Path, name: str) -> None:
    root = tmp_path / "audio"
    root.mkdir(exist_ok=True)
    (root / name).write_bytes(b"RIFF")


def test_manifest_loads_clips_with_metadata(tmp_path):
    _write_audio(tmp_path, "clip-a.wav")
    dataset = _load(tmp_path, [_clip("clip-a")])

    assert dataset.dataset_id == "test-set"
    assert dataset.version == "1.0.0"
    clip = dataset.by_id("clip-a")
    assert clip.reference_text == "hello world"
    assert clip.language == "en"
    assert clip.domain == "technical"
    assert clip.audio == tmp_path / "audio" / "clip-a.wav"
    assert clip.reference_segments[0].start == 0.0
    assert clip.reference_segments[0].end == 1.0


def test_duplicate_clip_id_is_rejected(tmp_path):
    _write_audio(tmp_path, "clip-a.wav")
    with pytest.raises(DatasetError, match="duplicate clip id"):
        _load(tmp_path, [_clip("clip-a"), _clip("clip-a")])


def test_unverified_reference_is_excluded_from_scoring(tmp_path):
    _write_audio(tmp_path, "clip-a.wav")
    _write_audio(tmp_path, "clip-b.wav")
    dataset = load_dataset(
        _manifest(
            tmp_path,
            [
                _clip("clip-a"),
                _clip(
                    "clip-b",
                    reference_text=None,
                    reference_segments=[],
                    reference_source="pending-human-verification",
                ),
            ],
        ),
        audio_root=tmp_path / "audio",
    )

    assert [clip.clip_id for clip in dataset.scored_clips()] == ["clip-a"]
    assert dataset.by_id("clip-b").reference_source == (
        "pending-human-verification"
    )


def test_pending_clip_may_have_no_reference_text(tmp_path):
    _write_audio(tmp_path, "clip-b.wav")
    dataset = load_dataset(
        _manifest(
            tmp_path,
            [
                _clip(
                    "clip-b",
                    reference_text=None,
                    reference_segments=[],
                    reference_source="pending-human-verification",
                )
            ],
        ),
        audio_root=tmp_path / "audio",
    )
    assert dataset.by_id("clip-b").reference_text is None


def test_scored_clip_without_reference_text_is_rejected(tmp_path):
    _write_audio(tmp_path, "clip-a.wav")
    with pytest.raises(DatasetError, match="reference_text"):
        _load(tmp_path, [_clip("clip-a", reference_text=None)])


def test_missing_audio_is_reported_not_raised(tmp_path):
    dataset = _load(tmp_path, [_clip("clip-a")])

    assert dataset.missing_audio == ("clip-a",)
    assert _load(tmp_path, [_clip("clip-a")]).clips  # still loaded


def test_scored_clips_are_ordered_by_clip_id(tmp_path):
    for name in ("clip-a", "clip-b", "clip-c"):
        _write_audio(tmp_path, f"{name}.wav")
    dataset = _load(tmp_path, [_clip("clip-c"), _clip("clip-a"), _clip("clip-b")])
    assert [clip.clip_id for clip in dataset.scored_clips()] == [
        "clip-a",
        "clip-b",
        "clip-c",
    ]


def test_unknown_reference_source_is_rejected(tmp_path):
    _write_audio(tmp_path, "clip-a.wav")
    with pytest.raises(DatasetError, match="reference_source"):
        _load(tmp_path, [_clip("clip-a", reference_source="vibes")])


def test_audio_root_can_be_overridden(tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "clip-a.wav").write_bytes(b"RIFF")
    dataset = load_dataset(
        _manifest(tmp_path, [_clip("clip-a")]), audio_root=other
    )
    assert dataset.missing_audio == ()
    assert dataset.by_id("clip-a").audio == other / "clip-a.wav"


def test_shipped_manifest_is_valid_and_has_verified_references():
    """The committed dataset must load and offer at least two scored clips."""
    from stt_platform.evaluation.datasets import default_dataset_path

    dataset = load_dataset(default_dataset_path())
    assert dataset.dataset_id == "stt-eval-core"
    scored = dataset.scored_clips()
    assert len(scored) >= 2, "a provider must never be ranked on one recording"
    for clip in scored:
        assert clip.reference_text and clip.reference_text.strip()
        assert clip.reference_source in {"synthetic-tts", "human"}
    assert any(
        clip.reference_source == "pending-human-verification"
        for clip in dataset.clips
    ), "the real-speech clip must be present but unscored"
