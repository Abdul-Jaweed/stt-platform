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
from collections.abc import Callable
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


#: The one-letter alphabet an alignment is serialized with. ``M`` is a match;
#: the artifact carries this legend next to every code, so a reader never has
#: to guess.
OP_CODES = {"equal": "M", "substitute": "S", "delete": "D", "insert": "I"}


@dataclass(frozen=True)
class AlignmentOp:
    """One step of the alignment: what happened to one unit of the reference,
    one unit of the hypothesis, or both."""

    kind: str  # equal | substitute | delete | insert
    reference_index: int | None
    hypothesis_index: int | None
    reference: str | None
    hypothesis: str | None


def align(reference: list[str], hypothesis: list[str]) -> list[AlignmentOp]:
    """Levenshtein alignment with a substitution/deletion/insertion breakdown.

    Ties prefer substitution, then deletion — a fixed, documented order, so
    the breakdown is reproducible rather than dependent on iteration order.
    ``edit_counts`` reads its numbers off this alignment, and the per-clip
    artifact stores the alignment itself, so the counts a run reports and the
    diff a reader sees cannot disagree.
    """
    rows = len(reference) + 1
    columns = len(hypothesis) + 1
    cost = [[0] * columns for _ in range(rows)]
    for i in range(1, rows):
        cost[i][0] = i
    for j in range(1, columns):
        cost[0][j] = j

    for i in range(1, rows):
        for j in range(1, columns):
            if reference[i - 1] == hypothesis[j - 1]:
                cost[i][j] = cost[i - 1][j - 1]
            else:
                cost[i][j] = 1 + min(
                    cost[i - 1][j - 1], cost[i - 1][j], cost[i][j - 1]
                )

    ops: list[AlignmentOp] = []
    i, j = rows - 1, columns - 1
    while i > 0 or j > 0:
        if (
            i > 0
            and j > 0
            and reference[i - 1] == hypothesis[j - 1]
            and cost[i][j] == cost[i - 1][j - 1]
        ):
            ops.append(
                AlignmentOp("equal", i - 1, j - 1, reference[i - 1], hypothesis[j - 1])
            )
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + 1:
            ops.append(
                AlignmentOp(
                    "substitute", i - 1, j - 1, reference[i - 1], hypothesis[j - 1]
                )
            )
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            ops.append(AlignmentOp("delete", i - 1, None, reference[i - 1], None))
            i -= 1
        else:
            ops.append(AlignmentOp("insert", None, j - 1, None, hypothesis[j - 1]))
            j -= 1

    ops.reverse()
    return ops


def alignment_code(ops: list[AlignmentOp]) -> str:
    """The alignment as the letters in :data:`OP_CODES`, one per step."""
    return "".join(OP_CODES[op.kind] for op in ops)


@dataclass(frozen=True)
class AlignmentReport:
    """A per-clip alignment, ready for the artifact: the code plus the two
    token lists it walks, in the order the normalizer produced them."""

    code: str
    reference_tokens: tuple[str, ...]
    hypothesis_tokens: tuple[str, ...]

    @classmethod
    def between(
        cls,
        reference: str,
        hypothesis: str,
        rules: NormalizationRules = DEFAULT_RULES,
    ) -> AlignmentReport:
        reference_tokens = tokens(reference, rules)
        hypothesis_tokens = tokens(hypothesis, rules)
        ops = align(reference_tokens, hypothesis_tokens)
        return cls(
            code=alignment_code(ops),
            reference_tokens=tuple(reference_tokens),
            hypothesis_tokens=tuple(hypothesis_tokens),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "alphabet": dict(OP_CODES),
            "reference_tokens": list(self.reference_tokens),
            "hypothesis_tokens": list(self.hypothesis_tokens),
        }


def edit_counts(reference: list[str], hypothesis: list[str]) -> ErrorCounts:
    """The counts behind :func:`align`, read off that same alignment."""
    kinds = [op.kind for op in align(reference, hypothesis)]
    return ErrorCounts(
        substitutions=kinds.count("substitute"),
        deletions=kinds.count("delete"),
        insertions=kinds.count("insert"),
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


def wer_from_counts(counts: ErrorCounts) -> float | None:
    """``(S + D + I) / reference units`` — the formula in ``METRIC_SPECS``.

    An empty reference has no defensible rate: ``None``, not ``0.0`` and not
    ``1.0`` — the caller must decide what to do with audio nobody transcribed.
    """
    if not counts.reference_units:
        return None
    return counts.errors / counts.reference_units


def cer_from_counts(counts: ErrorCounts) -> float | None:
    """The same ratio over characters (spaces removed) — see ``cer``."""
    return wer_from_counts(counts)


def _rate_usable(scores: list[AccuracyScore], attr: str) -> list[AccuracyScore]:
    return [item for item in scores if getattr(item, attr) is not None]


def wer_micro(scores: list[AccuracyScore]) -> float | None:
    """Total word errors over total reference words across the scored clips."""
    usable = _rate_usable(scores, "wer")
    if not usable:
        return None
    units = sum(item.word_errors.reference_units for item in usable)
    if not units:
        return None
    return sum(item.word_errors.errors for item in usable) / units


def wer_macro(scores: list[AccuracyScore]) -> float | None:
    """The mean of the per-clip WER values, each clip weighing the same."""
    usable = _rate_usable(scores, "wer")
    if not usable:
        return None
    return sum(item.wer for item in usable if item.wer is not None) / len(usable)


def cer_micro(scores: list[AccuracyScore]) -> float | None:
    """Total character errors over total reference characters."""
    usable = _rate_usable(scores, "cer")
    if not usable:
        return None
    units = sum(item.char_errors.reference_units for item in usable)
    if not units:
        return None
    return sum(item.char_errors.errors for item in usable) / units


def cer_macro(scores: list[AccuracyScore]) -> float | None:
    """The mean of the per-clip CER values."""
    usable = _rate_usable(scores, "cer")
    if not usable:
        return None
    return sum(item.cer for item in usable if item.cer is not None) / len(usable)


def score(
    reference: str,
    hypothesis: str,
    rules: NormalizationRules = DEFAULT_RULES,
) -> AccuracyScore:
    """WER/CER for one clip, with the counts and the alignment behind them."""
    reference_words = tokens(reference, rules)
    hypothesis_words = tokens(hypothesis, rules)
    word_errors = edit_counts(reference_words, hypothesis_words)
    char_errors = edit_counts(
        list(normalize(reference, rules).replace(" ", "")),
        list(normalize(hypothesis, rules).replace(" ", "")),
    )
    return AccuracyScore(
        wer=wer_from_counts(word_errors),
        cer=cer_from_counts(char_errors),
        word_errors=word_errors,
        char_errors=char_errors,
        rules=rules,
    )


def aggregate(scores: list[AccuracyScore]) -> dict[str, float | None]:
    """Micro (total errors / total units) and macro (mean of rates) WER/CER.

    Every number here comes from the named function that ``METRIC_SPECS``
    explains, so the explanation and the value cannot drift apart.
    """
    usable = [item for item in scores if item.wer is not None]
    return {
        "wer_micro": wer_micro(usable),
        "wer_macro": wer_macro(usable),
        "cer_micro": cer_micro(usable),
        "cer_macro": cer_macro(usable),
        "clips_scored": len(usable),
    }


# --------------------------------------------------------------------- #
# the explanations: bound to the code that computes the numbers
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class MetricSpec:
    """Everything a reader needs to interpret one number, next to the
    callable that produces it.

    ``compute`` is the implementation itself, not a description of it: the
    aggregate calls the same objects, and a test compares the two. Text
    fields explain what the number means, where it can mislead, and when it
    is absent — an accuracy metric nobody can interpret is a number, not a
    measurement.
    """

    name: str
    applies_to: str
    unit: str
    direction: str
    formula: str
    inputs: str
    normalization: str
    edge_behaviour: str
    limitations: str
    compute: Callable[..., float | None]

    def describe(self) -> dict[str, str]:
        return {
            "name": self.name,
            "applies_to": self.applies_to,
            "unit": self.unit,
            "direction": self.direction,
            "formula": self.formula,
            "inputs": self.inputs,
            "normalization": self.normalization,
            "edge_behaviour": self.edge_behaviour,
            "limitations": self.limitations,
            "computed_by": self.compute.__name__,
        }


_NORMALIZATION = (
    "stt-normalize-v1: Unicode NFKC, lowercase, punctuation stripped, "
    "whitespace collapsed. Deliberately no number expansion and no filler "
    "removal; the rules are recorded in every artifact."
)

METRIC_SPECS: dict[str, MetricSpec] = {
    spec.name: spec
    for spec in (
        MetricSpec(
            name="wer",
            applies_to="per clip",
            unit="ratio — word errors per reference word (0.0 is perfect)",
            direction="lower is better",
            formula="(substitutions + deletions + insertions) / reference words",
            inputs="the clip's reference transcript and the provider's transcript",
            normalization=_NORMALIZATION,
            edge_behaviour=(
                "an empty reference yields null, never 0.0; a transcript with "
                "extra words can push the ratio above 1.0"
            ),
            limitations=(
                "counts words, not meaning: 'not' for 'now' costs the same as "
                "'cat' for 'door'. Punctuation and casing are already gone, so "
                "it cannot score formatting."
            ),
            compute=wer_from_counts,
        ),
        MetricSpec(
            name="cer",
            applies_to="per clip",
            unit="ratio — character errors per reference character",
            direction="lower is better",
            formula=(
                "(substitutions + deletions + insertions) / reference characters "
                "(spaces removed)"
            ),
            inputs="the clip's reference transcript and the provider's transcript",
            normalization=_NORMALIZATION,
            edge_behaviour="an empty reference yields null, never 0.0",
            limitations=(
                "favours providers that split words differently; useful mainly "
                "for languages without word boundaries, or alongside WER."
            ),
            compute=cer_from_counts,
        ),
        MetricSpec(
            name="wer_micro",
            applies_to="run aggregate",
            unit="ratio — total word errors over total reference words",
            direction="lower is better",
            formula="sum(errors over scored clips) / sum(reference words over scored clips)",
            inputs="every clip the run scored (failed and skipped clips are absent, not zero)",
            normalization=_NORMALIZATION,
            edge_behaviour="null when the run scored no clips with a reference",
            limitations=(
                "long clips dominate: one long, bad clip can outweigh several "
                "short, clean ones. Pair it with wer_macro."
            ),
            compute=wer_micro,
        ),
        MetricSpec(
            name="wer_macro",
            applies_to="run aggregate",
            unit="ratio — mean of the per-clip WER values",
            direction="lower is better",
            formula="mean(wer per scored clip)",
            inputs="every clip the run scored",
            normalization=_NORMALIZATION,
            edge_behaviour="null when the run scored no clips",
            limitations=(
                "a two-word clip weighs as much as a two-minute one. Pair it "
                "with wer_micro."
            ),
            compute=wer_macro,
        ),
        MetricSpec(
            name="cer_micro",
            applies_to="run aggregate",
            unit="ratio — total character errors over total reference characters",
            direction="lower is better",
            formula="sum(character errors) / sum(reference characters) over scored clips",
            inputs="every clip the run scored",
            normalization=_NORMALIZATION,
            edge_behaviour="null when the run scored no clips",
            limitations="same weighting caveat as wer_micro, at character granularity",
            compute=cer_micro,
        ),
        MetricSpec(
            name="cer_macro",
            applies_to="run aggregate",
            unit="ratio — mean of the per-clip CER values",
            direction="lower is better",
            formula="mean(cer per scored clip)",
            inputs="every clip the run scored",
            normalization=_NORMALIZATION,
            edge_behaviour="null when the run scored no clips",
            limitations="same weighting caveat as wer_macro, at character granularity",
            compute=cer_macro,
        ),
    )
}
