"""Container bytes to PCM: one decoder per streaming session (Phase 8).

The client sends whatever its recorder produces -- streaming WebM/Opus in the
browser's case -- and the session core wants 16 kHz mono float windows. This
is that bridge, and it is deliberately one long-lived ffmpeg child per
session: every byte is decoded exactly once, instead of re-decoding the whole
take every tick the way the chunked preview does.

Two input shapes are supported:

* ``raw_pcm16=True`` -- the client already sends mono s16le (an
  ``AudioWorklet`` client, or a test): bytes are converted directly, no
  subprocess. A sample split across two writes survives, because a lone
  trailing byte is carried instead of being dropped.
* anything else -- ffmpeg probes the container from the stream, so WebM,
  WAV, MP3 and friends all work without the server guessing.

Failure policy: bytes that are not audio raise :class:`DecodeError` at
``close()`` when nothing could be decoded at all. A stream that produced audio
and *then* failed is reported through ``failed`` without throwing away what
was already transcribed -- a truncated tail must not lose the words before it.
"""
from __future__ import annotations

import subprocess
import threading
from collections import deque
from typing import IO

import numpy as np

__all__ = ["DecodeError", "FfmpegPcmDecoder", "pcm16_to_float"]

_CREATIONFLAGS = (
    subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
)
_READ_BYTES = 8192
_STDERR_KEEP_BYTES = 8192


def pcm16_to_float(data: bytes) -> np.ndarray:
    """Mono s16le bytes -> float32 samples in [-1, 1] (an odd tail is dropped)."""
    if len(data) % 2:
        data = data[:-1]
    if not data:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


class DecodeError(RuntimeError):
    def __init__(self, message: str, *, bytes_out: int, stderr_text: str) -> None:
        super().__init__(message)
        self.bytes_out = bytes_out
        self.stderr_text = stderr_text


class FfmpegPcmDecoder:
    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        raw_pcm16: bool = False,
        ffmpeg_bin: str = "ffmpeg",
    ) -> None:
        self.sample_rate = sample_rate
        self.raw_pcm16 = raw_pcm16
        self._ffmpeg_bin = ffmpeg_bin
        self._proc: subprocess.Popen[bytes] | None = None
        self._pump: threading.Thread | None = None
        self._stderr_pump: threading.Thread | None = None
        self._blocks: deque[np.ndarray] = deque()
        self._lock = threading.Lock()
        self._stderr: list[bytes] = []
        self._bytes_out = 0
        self._raw_carry = b""
        self._closed = False
        self._failed = False
        self._started = False
        self._returncode: int | None = None

    # ------------------------------------------------------------------ state
    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def bytes_out(self) -> int:
        return self._bytes_out

    @property
    def stderr_text(self) -> str:
        return b"".join(self._stderr).decode("utf-8", errors="replace")

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self.raw_pcm16:
            return
        command = [
            self._ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-vn",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            str(self.sample_rate),
            "pipe:1",
        ]
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_CREATIONFLAGS,
        )
        self._pump = threading.Thread(target=self._pump_stdout, daemon=True)
        self._pump.start()
        self._stderr_pump = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stderr_pump.start()

    def write(self, chunk: bytes) -> None:
        """Append container (or raw PCM) bytes. Never raises for a dead decoder."""
        if self._closed or not chunk:
            return
        if self._proc is None:
            self._enqueue(self._decode_bytes(chunk))
            return
        stdin = self._proc.stdin
        if stdin is None:
            return
        try:
            stdin.write(chunk)
            stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            self._failed = True

    def drain(self) -> list[np.ndarray]:
        """Everything decoded since the last call."""
        with self._lock:
            blocks = list(self._blocks)
            self._blocks.clear()
        return blocks

    def close(self, timeout_s: float = 15.0) -> None:
        """Flush and reap. Idempotent.

        Raises :class:`DecodeError` when the input could not be decoded at all
        (exit != 0 and no samples produced).
        """
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is None:  # raw PCM: the carry is an incomplete sample
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            self._returncode = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            self._returncode = proc.wait(timeout=5)
            self._failed = True
        if self._pump is not None:
            self._pump.join(timeout=5)
        if self._stderr_pump is not None:
            self._stderr_pump.join(timeout=2)
        if self._returncode not in (0, None):
            self._failed = True
            if self._bytes_out == 0:
                raise DecodeError(
                    "the stream could not be decoded",
                    bytes_out=0,
                    stderr_text=self.stderr_text.strip(),
                )

    # -------------------------------------------------------------- internals
    def _decode_bytes(self, data: bytes) -> np.ndarray:
        if self._raw_carry:
            data = self._raw_carry + data
            self._raw_carry = b""
        if len(data) % 2:
            # A sample split across two writes: keep the odd byte for the next
            # one rather than corrupting the stream or dropping the sample.
            self._raw_carry = data[-1:]
            data = data[:-1]
        return pcm16_to_float(data)

    def _enqueue(self, block: np.ndarray) -> None:
        if block.size == 0:
            return
        with self._lock:
            self._blocks.append(block)
        self._bytes_out += int(block.size) * 2

    def _pump_stdout(self) -> None:
        stdout: IO[bytes] | None = self._proc.stdout if self._proc else None
        if stdout is None:  # pragma: no cover - start() always sets it
            return
        while True:
            chunk = stdout.read(_READ_BYTES)
            if not chunk:
                break
            self._enqueue(self._decode_bytes(chunk))

    def _pump_stderr(self) -> None:
        stderr: IO[bytes] | None = self._proc.stderr if self._proc else None
        if stderr is None:  # pragma: no cover - start() always sets it
            return
        kept = 0
        while True:
            chunk = stderr.read(1024)
            if not chunk:
                break
            if kept < _STDERR_KEEP_BYTES:
                self._stderr.append(chunk)
                kept += len(chunk)
