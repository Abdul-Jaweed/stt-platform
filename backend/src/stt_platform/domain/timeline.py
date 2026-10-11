"""Timeline math. Every timestamp exposed by the platform refers to the
original audio timeline; chunked processing shifts results by their
offset without mutating the source segments."""
from stt_platform.schemas.transcript import TranscriptSegment


def shift_segments(
    segments: list[TranscriptSegment], offset_s: float
) -> list[TranscriptSegment]:
    """Return copies of ``segments`` with all timestamps shifted by ``offset_s``."""
    shifted: list[TranscriptSegment] = []
    for seg in segments:
        words = [
            w.model_copy(
                update={
                    "start": round(w.start + offset_s, 3),
                    "end": round(w.end + offset_s, 3),
                }
            )
            for w in seg.words
        ]
        shifted.append(
            seg.model_copy(
                update={
                    "start": round(seg.start + offset_s, 3),
                    "end": round(seg.end + offset_s, 3),
                    "words": words,
                }
            )
        )
    return shifted
