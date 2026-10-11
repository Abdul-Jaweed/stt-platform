from stt_platform.domain.timeline import shift_segments
from stt_platform.schemas.transcript import TranscriptSegment, TranscriptWord


def test_shift_adds_offset_to_segments_and_words():
    seg = TranscriptSegment(
        start=0.0,
        end=1.0,
        text="hello",
        words=[TranscriptWord(start=0.0, end=0.5, text="hello", confidence=0.9)],
    )
    shifted = shift_segments([seg], 5.0)
    assert shifted[0].start == 5.0
    assert shifted[0].end == 6.0
    assert shifted[0].words[0].start == 5.0
    assert shifted[0].words[0].end == 5.5
    # originals are not mutated
    assert seg.start == 0.0
    assert seg.words[0].start == 0.0


def test_shift_by_zero_returns_equal_copies():
    seg = TranscriptSegment(start=0.5, end=1.0, text="x")
    shifted = shift_segments([seg], 0.0)
    assert shifted == [seg]
    assert shifted[0] is not seg


def test_shift_preserves_order():
    segs = [
        TranscriptSegment(start=0.0, end=1.0, text="a"),
        TranscriptSegment(start=1.0, end=2.0, text="b"),
    ]
    shifted = shift_segments(segs, 10.0)
    assert [s.start for s in shifted] == [10.0, 11.0]
