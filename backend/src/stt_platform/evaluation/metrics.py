"""WER/CER with a normalization pipeline that is written down and recorded.

Accuracy numbers are only comparable if the normalization behind them is
known, so the rules are explicit, frozen in ``NormalizationRules``, and
stored in every evaluation result alongside the scores.

Rules (applied in this order): Unicode NFKC, lowercase, strip punctuation,
collapse whitespace. Deliberately *not* applied: number expansion (digits
stay digits), filler removal, and spoken-form rewriting — each would move
error mass out of the measurement and into the normalizer, and none of them
is reversible from a transcript.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+", re.UNICODE)


@dataclass(frozen=True)
class NormalizationRules:
    """The frozen normalization pipeline, recorded with every result."""

    name: str = "stt-normalize-v1"
    nfkc: bool = True
    lowercase: bool = True
    strip_punctuation: bool = True
    collapse_whitespace: bool = True

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "nfkc": self.nfkc,
            "lowercase": self.lowercase,
            "strip_punctuation": self.strip_punctuation,
            "collapse_whitespace": self.collapse_whitespace,
        }


DEFAULT_RULES = NormalizationRules()


def normalize(text: str, rules: NormalizationRules = DEFAULT_RULES) -> str:
    if rules.nfkc:
        text = unicodedata.normalize("NFKC", text)
    if rules.lowercase:
        text = text.lower()
    if rules.strip_punctuation:
        text = _PUNCTUATION.sub(" ", text)
    if rules.collapse_whitespace:
        text = _WHITESPACE.sub(" ", text).strip()
    return text


def tokens(text: str, rules: NormalizationRules = DEFAULT_RULES) -> list[str]:
    normalized = normalize(text, rules)
    return normalized.split(" ") if normalized else []


@dataclass(frozen=True)
class ErrorCounts:
    """Edit operations turning a reference into a hypothesis."""

    substitutions: int
    deletions: int
    insertions: int
    reference_units: int
    hypothesis_units: int

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    def as_dict(self) -> dict[str, int]:
        return {
            "substitutions": self.substitutions,
            "deletions": self.deletions,
            "insertions": self.insertions,
            "errors": self.errors,
            "reference_units": self.reference_units,
            "hypothesis_units": self.hypothesis_units,
        }


def edit_counts(reference: list[str], hypothesis: list[str]) -> ErrorCounts:
    """Levenshtein alignment with a substitution/deletion/insertion breakdown.

    Ties prefer substitution, then deletion — a fixed, documented order, so
    the breakdown is reproducible rather than dependent on iteration order.
    """
    rows = len(reference) + 1
    columns = len(hypothesis) + 1
    cost = [[0] * columns for _ in range(rows)]
    subs = [[0] * columns for _ in range(rows)]
    dels = [[0] * columns for _ in range(rows)]
    ins = [[0] * columns for _ in range(rows)]

    for i in range(1, rows):
        cost[i][0] = i
        dels[i][0] = i
    for j in range(1, columns):
        cost[0][j] = j
        ins[0][j] = j

    for i in range(1, rows):
        for j in range(1, columns):
            if reference[i - 1] == hypothesis[j - 1]:
                cost[i][j] = cost[i - 1][j - 1]
                subs[i][j] = subs[i - 1][j - 1]
                dels[i][j] = dels[i - 1][j - 1]
                ins[i][j] = ins[i - 1][j - 1]
                continue
            candidates = (
                (cost[i - 1][j - 1] + 1, 0),  # substitution
                (cost[i - 1][j] + 1, 1),  # deletion
                (cost[i][j - 1] + 1, 2),  # insertion
            )
            best_cost, operation = min(candidates, key=lambda item: (item[0], item[1]))
            cost[i][j] = best_cost
            if operation == 0:
                subs[i][j] = subs[i - 1][j - 1] + 1
                dels[i][j] = dels[i - 1][j - 1]
                ins[i][j] = ins[i - 1][j - 1]
            elif operation == 1:
                subs[i][j] = subs[i - 1][j]
                dels[i][j] = dels[i - 1][j] + 1
                ins[i][j] = ins[i - 1][j]
            else:
                subs[i][j] = subs[i][j - 1]
                dels[i][j] = dels[i][j - 1]
                ins[i][j] = ins[i][j - 1] + 1

    return ErrorCounts(
        substitutions=subs[rows - 1][columns - 1],
        deletions=dels[rows - 1][columns - 1],
        insertions=ins[rows - 1][columns - 1],
        reference_units=len(reference),
        hypothesis_units=len(hypothesis),
    )


@dataclass(frozen=True)
class AccuracyScore:
    """One WER/CER measurement, with the counts that produced it."""

    wer: float | None
    cer: float | None
    word_errors: ErrorCounts
    char_errors: ErrorCounts
    rules: NormalizationRules = field(default=DEFAULT_RULES)

    def as_dict(self) -> dict[str, object]:
        return {
            "wer": self.wer,
            "cer": self.cer,
            "normalization": self.rules.describe(),
            "word_errors": self.word_errors.as_dict(),
            "char_errors": self.char_errors.as_dict(),
        }


def score(
    reference: str,
    hypothesis: str,
    rules: NormalizationRules = DEFAULT_RULES,
) -> AccuracyScore:
    """WER/CER for one clip.

    An empty reference has no defensible rate: the result is ``None``, not
    ``0.0`` and not ``1.0`` — the caller must decide what to do with audio
    nobody transcribed.
    """
    reference_words = tokens(reference, rules)
    hypothesis_words = tokens(hypothesis, rules)
    word_errors = edit_counts(reference_words, hypothesis_words)
    char_errors = edit_counts(
        list(normalize(reference, rules).replace(" ", "")),
        list(normalize(hypothesis, rules).replace(" ", "")),
    )
    return AccuracyScore(
        wer=(
            word_errors.errors / word_errors.reference_units
            if word_errors.reference_units
            else None
        ),
        cer=(
            char_errors.errors / char_errors.reference_units
            if char_errors.reference_units
            else None
        ),
        word_errors=word_errors,
        char_errors=char_errors,
        rules=rules,
    )


def aggregate(scores: list[AccuracyScore]) -> dict[str, float | None]:
    """Micro (total errors / total units) and macro (mean of rates) WER/CER."""
    usable = [item for item in scores if item.wer is not None]
    if not usable:
        return {"wer_micro": None, "wer_macro": None, "cer_micro": None,
                "cer_macro": None, "clips_scored": 0}

    word_errors = sum(item.word_errors.errors for item in usable)
    word_units = sum(item.word_errors.reference_units for item in usable)
    char_errors = sum(item.char_errors.errors for item in usable)
    char_units = sum(item.char_errors.reference_units for item in usable)
    return {
        "wer_micro": word_errors / word_units if word_units else None,
        "wer_macro": sum(item.wer for item in usable if item.wer is not None)
        / len(usable),
        "cer_micro": char_errors / char_units if char_units else None,
        "cer_macro": sum(item.cer for item in usable if item.cer is not None)
        / len(usable),
        "clips_scored": len(usable),
    }
