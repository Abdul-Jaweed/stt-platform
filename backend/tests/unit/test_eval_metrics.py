"""WER/CER: hand-computed cases, edge cases, and a jiwer cross-check.

The cross-check matters: an accuracy metric that only agrees with itself is
not evidence. `jiwer` is a dev-only dependency used here to keep our
implementation honest, never at runtime.
"""
from __future__ import annotations

import jiwer
import pytest

from stt_platform.evaluation.metrics import (
    DEFAULT_RULES,
    NormalizationRules,
    aggregate,
    edit_counts,
    normalize,
    score,
    tokens,
)

# --------------------------------------------------------------- normalization


def test_normalization_folds_case_and_strips_punctuation():
    assert normalize("Hello, World!") == "hello world"


def test_normalization_collapses_whitespace_and_unicode_forms():
    assert normalize("  the\u00a0  cat  ") == "the cat"
    assert normalize("\ufb01le") == "file"  # NFKC: ligature fi


def test_normalization_does_not_expand_numbers():
    """Digits stay digits: expansion would move error mass into the normalizer."""
    assert normalize("42 dollars") == "42 dollars"
    assert tokens("42 dollars") == ["42", "dollars"]


def test_rules_are_recordable():
    described = DEFAULT_RULES.describe()
    assert described["name"] == "stt-normalize-v1"
    assert described["strip_punctuation"] is True
    assert set(described) == {
        "name",
        "nfkc",
        "lowercase",
        "strip_punctuation",
        "collapse_whitespace",
    }


def test_rules_can_be_weakened_explicitly():
    rules = NormalizationRules(strip_punctuation=False, name="no-punct-strip")
    assert normalize("Hello, World!", rules) == "hello, world!"
    assert rules.describe()["strip_punctuation"] is False


# ---------------------------------------------------------------------- WER


def test_identical_text_has_no_error():
    assert score("the cat sat", "the cat sat").wer == 0.0


def test_substitution_deletion_and_insertion_rates():
    assert score("the cat sat", "the cat sit").wer == pytest.approx(1 / 3)
    assert score("the cat sat", "the cat").wer == pytest.approx(1 / 3)
    assert score("the cat", "the cat sat").wer == pytest.approx(1 / 2)


def test_empty_hypothesis_is_one_hundred_percent_error():
    assert score("hello world", "").wer == 1.0


def test_empty_reference_has_no_defensible_rate():
    """No reference means no number — never 0.0 and never 1.0."""
    result = score("", "anything at all")
    assert result.wer is None
    assert result.cer is None


def test_punctuation_and_case_do_not_count_as_errors():
    assert score("Hello, World!", "hello world").wer == 0.0


def test_number_words_are_not_treated_as_digits():
    result = score("42 dollars", "forty two dollars")
    assert result.wer == pytest.approx(1.0)
    assert result.word_errors.substitutions == 1
    assert result.word_errors.insertions == 1


# ------------------------------------------------------------------ breakdown


def test_error_breakdown_counts_each_operation():
    counts = edit_counts(["a", "b", "c", "d"], ["a", "x", "c", "d", "e"])
    assert counts.substitutions == 1
    assert counts.deletions == 0
    assert counts.insertions == 1
    assert counts.errors == 2
    assert counts.reference_units == 4
    assert counts.hypothesis_units == 5


def test_error_breakdown_of_pure_deletion():
    counts = edit_counts(["a", "b", "c"], ["a", "c"])
    assert (counts.substitutions, counts.deletions, counts.insertions) == (0, 1, 0)


def test_score_dict_carries_counts_and_rules():
    payload = score("the cat sat", "the cat sit").as_dict()
    assert payload["normalization"]["name"] == "stt-normalize-v1"
    assert payload["word_errors"]["substitutions"] == 1
    assert payload["word_errors"]["errors"] == 1
    assert payload["wer"] == pytest.approx(1 / 3)


# ---------------------------------------------------------------------- CER


def test_cer_counts_character_edits():
    assert score("abcd", "abxd").cer == pytest.approx(0.25)
    assert score("abcd", "abcd").cer == 0.0


# ----------------------------------------------------------------- aggregate


def test_aggregate_reports_micro_and_macro_separately():
    long_clip = score(" ".join(["word"] * 10), " ".join(["word"] * 9 + ["other"]))
    short_clip = score("two words", "two wrong")
    summary = aggregate([long_clip, short_clip])

    assert summary["clips_scored"] == 2
    assert summary["wer_micro"] == pytest.approx(2 / 12)
    assert summary["wer_macro"] == pytest.approx((0.1 + 0.5) / 2)


def test_aggregate_skips_clips_without_a_reference():
    usable = score("hello world", "hello world")
    summary = aggregate([usable, score("", "no reference here")])
    assert summary["clips_scored"] == 1
    assert summary["wer_micro"] == 0.0


def test_aggregate_of_nothing_is_none_not_zero():
    summary = aggregate([])
    assert summary["wer_micro"] is None
    assert summary["wer_macro"] is None
    assert summary["clips_scored"] == 0


# ------------------------------------------------------------- jiwer agreement

PAIRS = [
    ("the quick brown fox", "the quick brown fox"),
    ("the quick brown fox", "the quick brown box"),
    ("the quick brown fox", "quick brown fox"),
    ("the quick brown fox", "the quick brown fox jumps"),
    ("a b c d e f", "a x c y e f"),
    ("hello world", ""),
    ("one two three four five", "one two three four five six seven"),
    ("Doctor Raman scheduled the follow up", "doctor raman scheduled a followup"),
]


@pytest.mark.parametrize(("reference", "hypothesis"), PAIRS)
def test_wer_agrees_with_jiwer(reference: str, hypothesis: str):
    ours = score(reference, hypothesis).wer
    theirs = jiwer.wer(normalize(reference), normalize(hypothesis))
    assert ours == pytest.approx(theirs, abs=1e-9)


@pytest.mark.parametrize(("reference", "hypothesis"), PAIRS)
def test_cer_agrees_with_jiwer(reference: str, hypothesis: str):
    ours = score(reference, hypothesis).cer
    reference_chars = normalize(reference).replace(" ", "")
    hypothesis_chars = normalize(hypothesis).replace(" ", "")
    theirs = jiwer.cer(reference_chars, hypothesis_chars)
    assert ours == pytest.approx(theirs, abs=1e-9)
