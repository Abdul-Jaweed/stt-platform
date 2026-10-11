"""Timestamp quality measurement.

Two failure modes look alike from a distance and must never be conflated:
a provider that returns *no* timings, and a provider whose timings are
*right*. Every metric here is ``None`` when it cannot be measured, so a
missing value can never be read as a perfect score.

Definitions (recorded with every evaluation):

* ``segment_availability`` / ``word_availability`` — fraction of units with
  a real span (``end > start``). Word availability is ``None`` when the
  result carries no words at all: "unknown", not "zero".
* ``boundary_error_s`` — mean absolute difference between matched reference
  and hypothesis boundaries (starts and ends pooled), plus the maximum.
  Matched pairwise in order, up to the shorter count.
* ``coverage`` — fraction of the reference duration covered by hypothesis
  spans (union of intervals, clamped to the reference span).
* ``monotonicity_violations`` — segments that overlap their predecessor or
  carry an empty span.
* ``degenerate_single_span`` — one segment covering (almost) the whole
  audio, i.e. timings that are nominal rather than measured.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

DEGENERATE_TOLERANCE_S = 0.25


@dataclass(frozen=True)
class TimestampReport:
    segments: int
    words: int
    segment_availability: float | None
    word_availability: float | None
    reference_boundaries: int
    hypothesis_boundaries: int
    matched_boundaries: int
    unmatched_reference_boundaries: int
    unmatched_hypothesis_boundaries: int
    boundary_error_s: float | None
    max_boundary_error_s: float | None
    coverage: float | None
    monotonicity_violations: int
    degenerate_single_span: bool
    granularity: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "segments": self.segments,
            "words": self.words,
            "segment_availability": self.segment_availability,
            "word_availability": self.word_availability,
            "reference_boundaries": self.reference_boundaries,
            "hypothesis_boundaries": self.hypothesis_boundaries,
            "matched_boundaries": self.matched_boundaries,
            "unmatched_reference_boundaries": self.unmatched_reference_boundaries,
            "unmatched_hypothesis_boundaries": self.unmatched_hypothesis_boundaries,
            "boundary_error_s": self.boundary_error_s,
            "max_boundary_error_s": self.max_boundary_error_s,
            "coverage": self.coverage,
            "monotonicity_violations": self.monotonicity_violations,
            "degenerate_single_span": self.degenerate_single_span,
            "granularity": self.granularity,
        }


def _span_ok(unit: Any) -> bool:
    try:
        return float(unit.end) > float(unit.start)
    except (AttributeError, TypeError, ValueError):
        return False


def _boundaries(segments: Iterable[Any]) -> list[float]:
    values: list[float] = []
    for segment in segments:
        values.append(float(segment.start))
        values.append(float(segment.end))
    return values


def _coverage(
    hypothesis_segments: Iterable[Any], reference_duration_s: float | None
) -> float | None:
    if not reference_duration_s or reference_duration_s <= 0:
        return None
    intervals = sorted(
        (float(segment.start), float(segment.end))
        for segment in hypothesis_segments
        if _span_ok(segment)
    )
    if not intervals:
        return 0.0
    covered = 0.0
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start > current_end:
            covered += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    covered += current_end - current_start
    return min(1.0, max(0.0, covered / reference_duration_s))


def _monotonicity_violations(segments: list[Any]) -> int:
    violations = 0
    previous_end: float | None = None
    for segment in segments:
        start, end = float(segment.start), float(segment.end)
        if end <= start or (previous_end is not None and start < previous_end):
            violations += 1
        previous_end = max(previous_end or 0.0, end)
    return violations


def evaluate_timestamps(
    *,
    reference_segments: Iterable[Any],
    hypothesis_segments: Iterable[Any],
    reference_duration_s: float | None,
    granularity: str | None = None,
) -> TimestampReport:
    """Measure what can be measured; leave the rest ``None``."""
    reference = list(reference_segments)
    hypothesis = list(hypothesis_segments)
    words = [word for segment in hypothesis for word in getattr(segment, "words", [])]

    reference_boundaries = _boundaries(reference)
    hypothesis_boundaries = _boundaries(hypothesis)
    matched = min(len(reference_boundaries), len(hypothesis_boundaries))
    differences = [
        abs(reference_boundaries[index] - hypothesis_boundaries[index])
        for index in range(matched)
    ]

    return TimestampReport(
        segments=len(hypothesis),
        words=len(words),
        segment_availability=(
            sum(1 for segment in hypothesis if _span_ok(segment)) / len(hypothesis)
            if hypothesis
            else None
        ),
        word_availability=(
            sum(1 for word in words if _span_ok(word)) / len(words) if words else None
        ),
        reference_boundaries=len(reference_boundaries),
        hypothesis_boundaries=len(hypothesis_boundaries),
        matched_boundaries=matched,
        unmatched_reference_boundaries=max(
            0, len(reference_boundaries) - matched
        ),
        unmatched_hypothesis_boundaries=max(
            0, len(hypothesis_boundaries) - matched
        ),
        boundary_error_s=(
            sum(differences) / len(differences) if differences else None
        ),
        max_boundary_error_s=max(differences) if differences else None,
        coverage=_coverage(hypothesis, reference_duration_s),
        monotonicity_violations=_monotonicity_violations(hypothesis),
        degenerate_single_span=bool(
            reference_duration_s
            and len(hypothesis) == 1
            and abs(float(hypothesis[0].end) - reference_duration_s)
            <= DEGENERATE_TOLERANCE_S
            and abs(float(hypothesis[0].start)) <= DEGENERATE_TOLERANCE_S
        ),
        granularity=granularity,
    )
