"""Reference datasets: versioned manifests with honest provenance.

A dataset is a JSON manifest, not a pile of audio: stable clip ids,
reference transcripts, language/domain/accent/noise metadata, reference
segment boundaries, and an explicit ``reference_source``. Audio lives on
the I: drive (never in git) and is recreated by ``scripts/make_eval_corpus.py``.

``reference_source`` decides whether a clip may be scored:

* ``synthetic-tts`` — the text was spoken by a TTS engine, so it is ground
  truth by construction;
* ``human`` — a human verified the transcript against the audio;
* ``pending-human-verification`` — present for wiring and smoke runs only,
  never scored, and never given an invented reference.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

SCORED_SOURCES = frozenset({"synthetic-tts", "human"})
KNOWN_SOURCES = SCORED_SOURCES | {"pending-human-verification"}

DEFAULT_DATASET_ID = "stt-eval-core"
DATASETS_DIR = Path(__file__).resolve().with_name("datasets")
AUDIO_ROOT_ENV = "STT_EVALUATION_AUDIO_ROOT"


class DatasetError(RuntimeError):
    """The manifest is unusable — never guess around it."""


@dataclass(frozen=True)
class ReferenceSegment:
    start: float
    end: float | None
    text: str


@dataclass(frozen=True)
class Clip:
    clip_id: str
    audio: Path
    reference_text: str | None
    duration_s: float | None
    language: str
    domain: str
    accent: str | None
    noise: str | None
    reference_source: str
    reference_segments: tuple[ReferenceSegment, ...]
    sha256: str | None = None

    @property
    def scored(self) -> bool:
        """Only a reference we can justify may ever produce an accuracy number."""
        return self.reference_source in SCORED_SOURCES


@dataclass(frozen=True)
class Dataset:
    dataset_id: str
    version: str
    language: str
    audio_root: Path
    clips: tuple[Clip, ...]
    missing_audio: tuple[str, ...]
    path: Path

    def by_id(self, clip_id: str) -> Clip:
        for clip in self.clips:
            if clip.clip_id == clip_id:
                return clip
        raise DatasetError(f"unknown clip id: {clip_id}")

    def scored_clips(self) -> tuple[Clip, ...]:
        """Scored clips in stable id order — the order every report uses."""
        return tuple(
            sorted(
                (clip for clip in self.clips if clip.scored),
                key=lambda clip: clip.clip_id,
            )
        )

    def missing_scored_audio(self) -> tuple[str, ...]:
        missing = set(self.missing_audio)
        return tuple(
            clip.clip_id for clip in self.scored_clips() if clip.clip_id in missing
        )


def default_dataset_path(dataset_id: str = DEFAULT_DATASET_ID) -> Path:
    return DATASETS_DIR / f"{dataset_id}.json"


def repo_root() -> Path:
    """Repository root, derived from this file's location."""
    return Path(__file__).resolve().parents[4]


def resolve_audio_root(manifest: dict, manifest_path: Path) -> Path:
    """Audio root: explicit argument > env override > manifest (repo-relative)."""
    override = os.environ.get(AUDIO_ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    declared = manifest.get("audio_root")
    if not declared:
        raise DatasetError(f"{manifest_path.name}: missing 'audio_root'")
    declared_path = Path(str(declared))
    if declared_path.is_absolute():
        return declared_path
    return (repo_root() / declared_path).resolve()


def load_dataset(
    path: Path | str, *, audio_root: Path | None = None
) -> Dataset:
    """Load and validate a manifest; report missing audio, never hide it."""
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise DatasetError(f"dataset manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{manifest_path.name}: invalid JSON: {exc}") from exc

    for key in ("dataset_id", "version", "clips"):
        if key not in manifest:
            raise DatasetError(f"{manifest_path.name}: missing '{key}'")

    root = audio_root or resolve_audio_root(manifest, manifest_path)
    clips: list[Clip] = []
    seen: set[str] = set()
    missing: list[str] = []

    for raw in manifest["clips"]:
        clip_id = str(raw.get("clip_id") or "")
        if not clip_id:
            raise DatasetError(f"{manifest_path.name}: clip without clip_id")
        if clip_id in seen:
            raise DatasetError(f"{manifest_path.name}: duplicate clip id {clip_id}")
        seen.add(clip_id)

        source = str(raw.get("reference_source") or "")
        if source not in KNOWN_SOURCES:
            raise DatasetError(
                f"{manifest_path.name}: {clip_id}: unknown reference_source "
                f"{source!r} (expected one of {sorted(KNOWN_SOURCES)})"
            )

        reference_text = raw.get("reference_text")
        if source in SCORED_SOURCES and not (
            isinstance(reference_text, str) and reference_text.strip()
        ):
            raise DatasetError(
                f"{manifest_path.name}: {clip_id}: a scored clip needs a "
                "non-empty reference_text"
            )

        duration = raw.get("duration_s")
        if duration is not None:
            duration = float(duration)
            if duration <= 0:
                raise DatasetError(
                    f"{manifest_path.name}: {clip_id}: duration_s must be > 0"
                )

        audio_name = raw.get("audio")
        if not audio_name:
            raise DatasetError(f"{manifest_path.name}: {clip_id}: missing audio")
        audio_path = (root / str(audio_name)).resolve()
        if not audio_path.is_file():
            missing.append(clip_id)

        segments = tuple(
            ReferenceSegment(
                start=float(segment["start"]),
                # A null end means "runs to the end of the clip": the
                # generator fills it in once the audio exists.
                end=(
                    float(segment["end"])
                    if segment.get("end") is not None
                    else duration
                ),
                text=str(segment.get("text") or ""),
            )
            for segment in raw.get("reference_segments") or []
        )

        clips.append(
            Clip(
                clip_id=clip_id,
                audio=audio_path,
                reference_text=reference_text if isinstance(reference_text, str) else None,
                duration_s=duration,
                language=str(raw.get("language") or manifest.get("language") or "en"),
                domain=str(raw.get("domain") or "unknown"),
                accent=raw.get("accent"),
                noise=raw.get("noise"),
                reference_source=source,
                reference_segments=segments,
                sha256=raw.get("sha256"),
            )
        )

    return Dataset(
        dataset_id=str(manifest["dataset_id"]),
        version=str(manifest["version"]),
        language=str(manifest.get("language") or "en"),
        audio_root=root,
        clips=tuple(clips),
        missing_audio=tuple(sorted(missing)),
        path=manifest_path,
    )


def datasets_dir(configured: Path | None = None) -> Path:
    """Where manifests live: settings first, then the packaged directory."""
    return Path(configured) if configured else DATASETS_DIR


def dataset_path(dataset_id: str, *, directory: Path | None = None) -> Path:
    """Resolve a dataset id to its manifest path (no traversal)."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "", dataset_id or "")
    if not safe or safe != dataset_id:
        raise DatasetError(f"invalid dataset id: {dataset_id!r}")
    return datasets_dir(directory) / f"{safe}.json"


def available_datasets(*, directory: Path | None = None) -> list[str]:
    root = datasets_dir(directory)
    if not root.is_dir():
        return []
    return sorted(path.stem for path in root.glob("*.json"))
