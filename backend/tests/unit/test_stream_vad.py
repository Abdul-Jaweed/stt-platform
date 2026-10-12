"""T1 - utterance segmentation for the streaming path (RED first).

The segmenter consumes 16 kHz mono float samples and answers two questions:
which utterances have CLOSED (stable, ready to decode once and emit as
``final``), and what window is still open (re-decoded for ``partial``).
Tests use synthetic signals because the property under test is geometry,
not recognition: a tone is speech, zeros are silence.
"""
from __future__ import annotations

import numpy as np

from stt_platform.streaming.vad import ClosedWindow, UtteranceSegmenter, VadConfig

SR = 16000


def tone(seconds: float, amplitude: float = 0.3, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * SR), dtype=np.float64) / SR
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def segmenter(**overrides) -> UtteranceSegmenter:
    config = VadConfig(sample_rate=SR, **overrides)
    return UtteranceSegmenter(config)


def ms(samples: int) -> float:
    return samples / SR * 1000.0


def test_silence_alone_never_opens_a_window():
    seg = segmenter()
    assert seg.feed(silence(2.0)) == []
    assert seg.open_window is None


def test_a_short_blip_is_not_speech():
    # 100 ms of tone is under min_speech_ms (240): a keyboard click, not a word.
    seg = segmenter()
    assert seg.feed(tone(0.1)) == []
    seg.feed(silence(1.0))
    assert seg.open_window is None


def test_speech_then_silence_closes_one_window_at_the_speech_end():
    seg = segmenter()
    closed = seg.feed(tone(1.0))
    assert closed == []
    assert seg.open_window is not None, "speech must open a window"

    closed = seg.feed(silence(0.8))
    assert len(closed) == 1
    window = closed[0]
    assert isinstance(window, ClosedWindow)
    assert window.reason == "silence"
    # the window ends where the trailing silence began, not where it ended
    assert abs(ms(window.end_sample) - 1000.0) < 40.0
    assert abs(ms(window.start_sample) - 0.0) < 40.0
    assert seg.open_window is None


def test_silence_shorter_than_the_hold_does_not_close_a_window():
    # A pause inside a sentence must not close it: 300 ms of silence < 600 ms.
    seg = segmenter()
    seg.feed(tone(0.6))
    assert seg.feed(silence(0.3)) == []
    assert seg.open_window is not None
    seg.feed(tone(0.6))
    closed = seg.feed(silence(0.7))
    assert len(closed) == 1
    assert abs(ms(closed[0].end_sample) - 1500.0) < 80.0, "pause is inside the window"


def test_a_long_monologue_rolls_over_without_gaps_or_overlaps():
    seg = segmenter(max_window_ms=2000, silence_ms=600)
    closed = seg.feed(tone(5.0))
    assert [w.reason for w in closed] == ["rollover", "rollover"]
    assert closed[0].end_sample == closed[1].start_sample
    assert seg.open_window is not None
    seg.feed(silence(0.8))
    final = seg.feed([]) or []
    assert final == []
    assert seg.open_window is None, "the last rollover window closes on silence"


def test_chunking_does_not_change_the_result():
    """The same signal fed in different-sized pieces is the same signal."""
    signal = np.concatenate([tone(0.7), silence(0.7), tone(0.5), silence(1.0)])
    loud = segmenter()
    whole = loud.feed(signal)

    quiet = segmenter()
    pieces: list[ClosedWindow] = []
    step = 613  # deliberately not a divisor of anything
    for start in range(0, len(signal), step):
        pieces.extend(quiet.feed(signal[start : start + step]))

    assert [(w.start_sample, w.end_sample, w.reason) for w in pieces] == [
        (w.start_sample, w.end_sample, w.reason) for w in whole
    ]


def test_flush_closes_the_open_window_for_the_stop_button():
    seg = segmenter()
    seg.feed(tone(0.9))
    window = seg.flush()
    assert window is not None
    assert window.reason == "flush"
    assert abs(ms(window.end_sample) - 900.0) < 40.0
    assert seg.flush() is None, "flushing twice is not a second utterance"


def test_flush_without_speech_is_none():
    seg = segmenter()
    seg.feed(silence(1.0))
    assert seg.flush() is None
