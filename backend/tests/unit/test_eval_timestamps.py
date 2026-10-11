"""Timestamp quality: availability, boundary error, coverage, monotonicity.

"Not reported by the provider" must never look like "zero error", so every
metric here is ``None`` when it is not measurable.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from stt_platform.evaluation.timestamps import evaluate_timestamps


def seg(start: float, end: float, *, words: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end, words=words or [])


def word(start: float, end: float) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end)


def test_availability_counts_words_and_segments_separately():
    report = evaluate_timestamps(
        reference_segments=[],
        hypothesis_segments=[
            seg(0.0, 2.0, words=[word(0.0, 1.0), word(1.0, 2.0)]),
            seg(2.0, 4.0, words=[word(2.0, 3.0), word(3.0, 4.0)]),
        ],
        reference_duration_s=4.0,
    )
    assert report.segment_availability == 1.0
    assert report.word_availability == 1.0
    assert report.words == 4


def test_words_without_timestamps_are_zero_not_unknown():
    report = evaluate_timestamps(
        reference_segments=[],
        hypothesis_segments=[seg(0.0, 2.0, words=[word(0.0, 0.0), word(1.0, 1.0)])],
        reference_duration_s=2.0,
    )
    assert report.word_availability == 0.0
    assert report.words == 2


def test_no_words_at_all_is_unknown_not_zero():
    report = evaluate_timestamps(
        reference_segments=[],
        hypothesis_segments=[seg(0.0, 2.0)],
        reference_duration_s=2.0,
    )
    assert report.word_availability is None
    assert report.words == 0


def test_boundary_error_is_measured_against_reference_boundaries():
    report = evaluate_timestamps(
        reference_segments=[seg(0.0, 5.0)],
        hypothesis_segments=[seg(0.2, 4.8)],
        reference_duration_s=5.0,
    )
    assert report.matched_boundaries == 2
    assert report.boundary_error_s == pytest.approx(0.2)
    assert report.max_boundary_error_s == pytest.approx(0.2)


def test_boundary_error_without_reference_is_none_not_zero():
    report = evaluate_timestamps(
        reference_segments=[],
        hypothesis_segments=[seg(0.0, 5.0)],
        reference_duration_s=5.0,
    )
    assert report.boundary_error_s is None
    assert report.max_boundary_error_s is None
    assert report.matched_boundaries == 0


def test_mismatched_boundary_counts_are_reported_honestly():
    report = evaluate_timestamps(
        reference_segments=[seg(0.0, 5.0)],
        hypothesis_segments=[seg(0.0, 2.5), seg(2.5, 5.0)],
        reference_duration_s=5.0,
    )
    assert report.reference_boundaries == 2
    assert report.hypothesis_boundaries == 4
    assert report.matched_boundaries == 2
    assert report.unmatched_reference_boundaries == 0
    assert report.unmatched_hypothesis_boundaries == 2


def test_coverage_is_partial_and_capped():
    partial = evaluate_timestamps(
        reference_segments=[],
        hypothesis_segments=[seg(0.0, 2.5)],
        reference_duration_s=5.0,
    )
    assert partial.coverage == pytest.approx(0.5)

    over = evaluate_timestamps(
        reference_segments=[],
        hypothesis_segments=[seg(0.0, 9.0)],
        reference_duration_s=5.0,
    )
    assert over.coverage == pytest.approx(1.0)


def test_monotonicity_violations_are_counted():
    report = evaluate_timestamps(
        reference_segments=[],
        hypothesis_segments=[seg(0.0, 3.0), seg(2.0, 4.0), seg(4.0, 4.0)],
        reference_duration_s=4.0,
    )
    assert report.monotonicity_violations == 2  # overlap + empty span


def test_degenerate_single_span_is_flagged():
    report = evaluate_timestamps(
        reference_segments=[],
        hypothesis_segments=[seg(0.0, 10.0)],
        reference_duration_s=10.0,
    )
    assert report.degenerate_single_span is True

    healthy = evaluate_timestamps(
        reference_segments=[],
        hypothesis_segments=[seg(0.0, 4.0), seg(4.0, 10.0)],
        reference_duration_s=10.0,
    )
    assert healthy.degenerate_single_span is False


def test_report_is_recordable():
    payload = evaluate_timestamps(
        reference_segments=[seg(0.0, 5.0)],
        hypothesis_segments=[seg(0.1, 4.9, words=[word(0.1, 1.0)])],
        reference_duration_s=5.0,
        granularity="word",
    ).as_dict()
    assert payload["granularity"] == "word"
    assert payload["matched_boundaries"] == 2
    assert set(payload) >= {
        "segments",
        "words",
        "segment_availability",
        "word_availability",
        "boundary_error_s",
        "max_boundary_error_s",
        "coverage",
        "monotonicity_violations",
        "degenerate_single_span",
        "granularity",
    }
