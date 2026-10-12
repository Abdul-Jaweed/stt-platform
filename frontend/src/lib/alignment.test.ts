/**
 * The alignment walker, tested against the artifact contract.
 *
 * Every artifact clip carries the same wording a run scores: a `code` of
 * one-letter operations, the alphabet that names them, and the two token
 * lists it walks. The diff a reader sees and the counts a run reports must
 * be read off that same alignment — so this module walks it once, loudly:
 * a code that disagrees with its token lists is a corrupt artifact, and
 * silently dropping or clamping tokens would draw a diff that never
 * happened.
 */
import { describe, expect, test } from "vitest";

import { alignmentCounts, diffFromAlignment } from "./alignment";
import type { Alignment } from "./alignment";

/** The legend the backend stores next to every code (metrics.OP_CODES). */
const ALPHABET = { equal: "M", substitute: "S", delete: "D", insert: "I" };

function alignment(
  code: string,
  reference_tokens: string[],
  hypothesis_tokens: string[],
): Alignment {
  return { code, alphabet: ALPHABET, reference_tokens, hypothesis_tokens };
}

describe("diffFromAlignment", () => {
  test("a mixed code maps every letter to its operation", () => {
    const parts = diffFromAlignment(
      alignment("MSMDI", ["the", "quick", "brown", "fox"], ["the", "quik", "brown", "jumps"]),
    );

    expect(parts).toEqual([
      { kind: "equal", reference: "the", hypothesis: "the" },
      { kind: "substitute", reference: "quick", hypothesis: "quik" },
      { kind: "equal", reference: "brown", hypothesis: "brown" },
      { kind: "delete", reference: "fox" },
      { kind: "insert", hypothesis: "jumps" },
    ]);
  });

  test("the counts match the run's own word_errors for the same clip", () => {
    // Three clips as the backend's aligner actually produces them: a
    // substitution pair, a deletion, and trailing insertions. The word_errors
    // beside each alignment are the numbers a run reports for that clip.
    const clips = [
      {
        alignment: alignment("MMSS", ["the", "quick", "brown", "fox"], ["the", "quick", "fox", "jumps"]),
        word_errors: {
          substitutions: 2,
          deletions: 0,
          insertions: 0,
          errors: 2,
          reference_units: 4,
          hypothesis_units: 4,
        },
      },
      {
        alignment: alignment("MMDM", ["the", "quick", "brown", "fox"], ["the", "quick", "fox"]),
        word_errors: {
          substitutions: 0,
          deletions: 1,
          insertions: 0,
          errors: 1,
          reference_units: 4,
          hypothesis_units: 3,
        },
      },
      {
        alignment: alignment(
          "MMMMII",
          ["the", "quick", "brown", "fox"],
          ["the", "quick", "brown", "fox", "jumps", "away"],
        ),
        word_errors: {
          substitutions: 0,
          deletions: 0,
          insertions: 2,
          errors: 2,
          reference_units: 4,
          hypothesis_units: 6,
        },
      },
    ];

    for (const clip of clips) {
      const counts = alignmentCounts(diffFromAlignment(clip.alignment));
      expect(counts).toEqual({
        substitutions: clip.word_errors.substitutions,
        deletions: clip.word_errors.deletions,
        insertions: clip.word_errors.insertions,
        errors: clip.word_errors.errors,
      });
      // And the walk consumed exactly the units the run says it did.
      expect(clip.alignment.reference_tokens).toHaveLength(clip.word_errors.reference_units);
      expect(clip.alignment.hypothesis_tokens).toHaveLength(clip.word_errors.hypothesis_units);
    }
  });

  test("an unknown letter throws instead of being ignored", () => {
    const corrupt = alignment("MX", ["the", "quick"], ["the", "quick"]);

    expect(() => diffFromAlignment(corrupt)).toThrow(/unknown operation 'X'/);
  });

  test("a code that outruns a token list throws", () => {
    const corrupt = alignment("MMM", ["the", "quick"], ["the", "quick", "fox"]);

    expect(() => diffFromAlignment(corrupt)).toThrow(/overruns the reference tokens/);
  });

  test("leftover tokens throw instead of being silently dropped", () => {
    const corrupt = alignment("M", ["the", "quick"], ["the", "quick"]);

    expect(() => diffFromAlignment(corrupt)).toThrow(/does not consume every token/);
  });

  test("an alignment without an alphabet is rejected, not guessed", () => {
    const corrupt = {
      code: "M",
      reference_tokens: ["the"],
      hypothesis_tokens: ["the"],
    } as unknown as Alignment;

    expect(() => diffFromAlignment(corrupt)).toThrow(/missing its alphabet/);
  });
});

describe("alignmentCounts", () => {
  test("counts only errors, and errors is their sum", () => {
    const counts = alignmentCounts(
      diffFromAlignment(
        alignment("MMSDDI", ["a", "b", "c", "d", "e"], ["a", "b", "x", "y"]),
      ),
    );

    expect(counts).toEqual({
      substitutions: 1,
      deletions: 2,
      insertions: 1,
      errors: 4,
    });
  });
});
