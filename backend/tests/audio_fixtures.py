"""Synthetic audio fixtures generated with ffmpeg at test time.

No audio file is ever committed to this repository (ADR-007): committed
fixtures are synthesized here; real-speech tests use audio/sample.wav
when present and skip otherwise.
"""
import subprocess
from pathlib import Path

_CREATIONFLAGS = (
    subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
)


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(
        cmd, capture_output=True, timeout=60, creationflags=_CREATIONFLAGS
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"ffmpeg failed ({' '.join(cmd)}): {detail}")


def make_wav(
    path: Path, seconds: float = 2.0, freq: int = 440, sample_rate: int = 16000
) -> Path:
    """Mono sine-wave WAV; returns the file path."""
    out = path / f"tone_{freq}hz_{seconds}s.wav" if path.is_dir() else path
    _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
            "-ar", str(sample_rate), "-ac", "1", str(out),
        ]
    )
    return out


def make_mp3(path: Path, seconds: float = 2.0, freq: int = 440) -> Path:
    """Mono MP3 sine wave; returns the file path."""
    out = path / "tone.mp3" if path.is_dir() else path
    _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
            "-ar", "44100", "-ac", "1", "-codec:a", "libmp3lame", str(out),
        ]
    )
    return out
