"""T3 - container bytes to PCM, one decoder per session (RED first).

The browser sends whatever ``MediaRecorder`` produces (streaming WebM/Opus);
the session core wants 16 kHz mono float windows. This layer is the bridge,
and its contract is small: every byte in is decoded once, a sample split
across two writes survives, and input that is not audio fails loudly instead
of quietly decoding to silence.
"""
from __future__ import annotations

import numpy as np
import pytest

from stt_platform.streaming.decoder import (
    DecodeError,
    FfmpegPcmDecoder,
    pcm16_to_float,
)
from tests.audio_fixtures import make_streaming_webm, make_wav

SR = 16000


def sine_pcm16(seconds: float, freq: float = 220.0, amplitude: float = 0.4) -> bytes:
    t = np.arange(int(seconds * SR), dtype=np.float64) / SR
    return (amplitude * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2").tobytes()


def collected(decoder: FfmpegPcmDecoder) -> np.ndarray:
    blocks = decoder.drain()
    return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


def test_pcm16_bytes_become_float_samples():
    samples = pcm16_to_float(sine_pcm16(0.1))
    assert samples.dtype == np.float32
    assert samples.size == int(0.1 * SR)
    assert 0.3 < float(np.max(np.abs(samples))) < 0.5


def test_a_sample_split_across_two_writes_survives():
    """Raw PCM arrives in whatever sized blocks the socket gave us."""
    decoder = FfmpegPcmDecoder(raw_pcm16=True)
    decoder.start()
    data = sine_pcm16(0.05)
    for start in range(0, len(data), 3):  # odd-sized writes split samples
        decoder.write(data[start : start + 3])
    samples = collected(decoder)
    decoder.close()
    assert samples.size == len(data) // 2, "no sample was lost or invented"
    assert abs(float(samples[0]) - np.frombuffer(data[:2], "<i2")[0] / 32768) < 1e-6


def feed_and_decode(decoder: FfmpegPcmDecoder, payload: bytes, chunk: int = 4096) -> np.ndarray:
    """Write the whole stream, close (which flushes and reaps ffmpeg), then read.

    Draining before ``close()`` would collect only what the pump thread had
    managed to grab so far -- the live path drains continuously, a test must
    not guess at ffmpeg's timing.
    """
    decoder.start()
    for start in range(0, len(payload), chunk):
        decoder.write(payload[start : start + chunk])
    decoder.close()
    return collected(decoder)


def test_a_wav_stream_decodes_to_its_duration(tmp_path):
    wav = make_wav(tmp_path, seconds=1.0)
    decoder = FfmpegPcmDecoder()
    samples = feed_and_decode(decoder, wav.read_bytes())
    duration = samples.size / SR
    assert abs(duration - 1.0) < 0.05, duration


def test_a_streaming_webm_decodes_like_mediarecorder_produces(tmp_path):
    webm = make_streaming_webm(tmp_path, seconds=2.0)
    decoder = FfmpegPcmDecoder()
    samples = feed_and_decode(decoder, webm.read_bytes())
    duration = samples.size / SR
    # Opus adds priming/padding: a couple of hundred ms of slop is expected.
    assert abs(duration - 2.0) < 0.25, duration
    assert float(np.sqrt(np.mean(samples**2))) > 0.05, "decoded to silence"


def test_input_that_is_not_audio_fails_loudly():
    decoder = FfmpegPcmDecoder()
    decoder.start()
    decoder.write(b"this is definitely not a container " * 2000)
    with pytest.raises(DecodeError) as caught:
        decoder.close()
    assert caught.value.bytes_out == 0
    assert "Invalid" in caught.value.stderr_text or "error" in caught.value.stderr_text.lower()


def test_close_is_idempotent_and_write_after_close_is_a_no_op():
    decoder = FfmpegPcmDecoder(raw_pcm16=True)
    decoder.start()
    decoder.write(sine_pcm16(0.02))
    decoder.close()
    decoder.close()  # a second close must not raise
    decoder.write(sine_pcm16(0.02))  # nor may a late write explode the session


def test_draining_twice_yields_nothing_the_second_time():
    decoder = FfmpegPcmDecoder(raw_pcm16=True)
    decoder.start()
    decoder.write(sine_pcm16(0.02))
    assert collected(decoder).size > 0
    assert collected(decoder).size == 0
    decoder.close()
