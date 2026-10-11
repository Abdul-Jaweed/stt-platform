"""ffprobe-based media inspection.

Actual media content is inspected; filename extensions are never trusted
(spec §8). Every failure surfaces as :class:`AudioValidationError` with a
stable code so the API layer can map it to a clean 4xx.
"""
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from stt_platform.domain.errors import AudioValidationError

PROBE_TIMEOUT_S = 15
# A full decode is only run when ffprobe reports no duration at all (streaming
# containers); uploads are size-bounded, so this stays cheap in practice.
MEASURE_TIMEOUT_S = 120

_CREATIONFLAGS = (
    subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
)


@dataclass(frozen=True)
class AudioInfo:
    format_name: str
    codec: str | None
    sample_rate: int
    channels: int
    duration_s: float
    size_bytes: int


def _ffprobe_json(path: Path) -> dict:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
            creationflags=_CREATIONFLAGS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioValidationError("UNDECODABLE", f"ffprobe failed: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()[:200]
        raise AudioValidationError(
            "UNDECODABLE", f"ffprobe rejected the file: {detail}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AudioValidationError(
            "UNDECODABLE", "ffprobe returned invalid JSON"
        ) from exc


def _measure_duration_s(path: Path) -> float:
    """Decode the stream and read ffmpeg's own clock.

    Streaming containers carry no Duration element — a browser's
    ``MediaRecorder`` WebM is the everyday example — so ffprobe has nothing to
    report and the decoded timeline is the only honest source left. Returns
    0.0 when even that fails; the caller turns that into ZERO_DURATION.
    """
    cmd = [
        "ffmpeg",
        "-v", "error",
        "-nostdin",
        "-i", str(path),
        "-map", "0:a:0",
        "-f", "null",
        "-progress", "pipe:1",
        "-nostats",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=MEASURE_TIMEOUT_S,
            check=False,
            creationflags=_CREATIONFLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    if proc.returncode != 0:
        return 0.0
    measured = 0.0
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        key, _, value = line.partition("=")
        if key.strip() != "out_time_us":
            continue
        try:
            measured = max(measured, int(value.strip()) / 1_000_000)
        except ValueError:
            continue
    return measured


def inspect_audio(path: Path) -> AudioInfo:
    """Return metadata for the real content of ``path``.

    Raises :class:`AudioValidationError` with code one of
    FILE_NOT_FOUND, EMPTY, UNDECODABLE, NO_AUDIO_STREAM, ZERO_DURATION.
    """
    path = Path(path)
    if not path.is_file():
        raise AudioValidationError("FILE_NOT_FOUND", f"no such file: {path}")
    size_bytes = path.stat().st_size
    if size_bytes == 0:
        raise AudioValidationError("EMPTY", "file is empty")

    data = _ffprobe_json(path)
    streams = data.get("streams") or []
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio_streams:
        raise AudioValidationError("NO_AUDIO_STREAM", "no audio stream found")
    stream = audio_streams[0]
    fmt = data.get("format") or {}

    try:
        duration_s = float(fmt.get("duration") or stream.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration_s = 0.0
    if duration_s <= 0:
        duration_s = _measure_duration_s(path)
    if duration_s <= 0:
        raise AudioValidationError(
            "ZERO_DURATION", "could not determine a positive duration"
        )

    try:
        sample_rate = int(stream.get("sample_rate") or 0)
        channels = int(stream.get("channels") or 0)
    except (TypeError, ValueError):
        sample_rate, channels = 0, 0
    if sample_rate <= 0:
        raise AudioValidationError("UNDECODABLE", "invalid sample rate")
    if channels <= 0:
        raise AudioValidationError("UNDECODABLE", "invalid channel count")

    return AudioInfo(
        format_name=str(fmt.get("format_name") or stream.get("codec_name") or "unknown"),
        codec=stream.get("codec_name"),
        sample_rate=sample_rate,
        channels=channels,
        duration_s=duration_s,
        size_bytes=size_bytes,
    )
