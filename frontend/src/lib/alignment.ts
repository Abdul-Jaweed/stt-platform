/**
 * The artifact's alignment, walked once.
 *
 * Every scored clip in a stored artifact carries the alignment a run scored
 * from: a `code` of one-letter operations, the alphabet that names them, and
 * the two token lists it walks. This module turns that into an ordered diff —
 * the same walk the backend counts errors with, so the diff a reader sees and
 * the numbers a run reports cannot drift apart.
 *
 * The walk is strict on purpose. A code that disagrees with its token lists
 * is a corrupt artifact; dropping or clamping tokens would draw a diff that
 * never happened, with agreement where there is none. So it throws, loudly.
 */

export type DiffKind = "equal" | "substitute" | "delete" | "insert";

/** One step of the alignment: what happened to one unit of the reference,
 * one unit of the hypothesis, or both. */
export interface DiffPart {
  kind: DiffKind;
  reference?: string;
  hypothesis?: string;
}

/** The one-letter legend stored next to every code (`metrics.OP_CODES`). */
export interface AlignmentAlphabet {
  equal: string;
  substitute: string;
  delete: string;
  insert: string;
}

/** `clip.alignment` in `GET /v1/evaluations/{id}/artifact`. */
export interface Alignment {
  code: string;
  alphabet: AlignmentAlphabet;
  reference_tokens: string[];
  hypothesis_tokens: string[];
}

export interface AlignmentCounts {
  substitutions: number;
  deletions: number;
  insertions: number;
  errors: number;
}

function alphabetLegend(alphabet: AlignmentAlphabet): string {
  return (
    `${alphabet.equal}=match, ${alphabet.substitute}=substitute, ` +
    `${alphabet.delete}=delete, ${alphabet.insert}=insert`
  );
}

function take(
  list: string[],
  index: number,
  which: string,
  code: string,
  position: number,
): string {
  if (index >= list.length) {
    throw new Error(
      `alignment code '${code}' overruns the ${which} tokens at position ` +
        `${position}: the ${which} has only ${list.length} token` +
        `${list.length === 1 ? "" : "s"}`,
    );
  }
  return list[index];
}

/**
 * Walk `alignment.code` left to right, pairing tokens the way the code says.
 *
 * `M` consumes one token from each list (equal), `S` one from each (they
 * differ), `D` a reference token alone (deleted), `I` a hypothesis token
 * alone (inserted). Anything else — an unknown letter, a step that outruns a
 * list, or tokens left over when the code ends — throws, because the code and
 * the lists must describe the same alignment.
 */
export function diffFromAlignment(alignment: Alignment): DiffPart[] {
  const { code, alphabet } = alignment;
  if (!alphabet) {
    throw new Error(
      "alignment is missing its alphabet: the code's letters cannot be read",
    );
  }
  const reference = alignment.reference_tokens;
  const hypothesis = alignment.hypothesis_tokens;
  const parts: DiffPart[] = [];
  let r = 0;
  let h = 0;

  for (let position = 0; position < code.length; position += 1) {
    const op = code[position];
    if (op === alphabet.equal) {
      parts.push({
        kind: "equal",
        reference: take(reference, r, "reference", code, position),
        hypothesis: take(hypothesis, h, "hypothesis", code, position),
      });
      r += 1;
      h += 1;
    } else if (op === alphabet.substitute) {
      parts.push({
        kind: "substitute",
        reference: take(reference, r, "reference", code, position),
        hypothesis: take(hypothesis, h, "hypothesis", code, position),
      });
      r += 1;
      h += 1;
    } else if (op === alphabet.delete) {
      parts.push({
        kind: "delete",
        reference: take(reference, r, "reference", code, position),
      });
      r += 1;
    } else if (op === alphabet.insert) {
      parts.push({
        kind: "insert",
        hypothesis: take(hypothesis, h, "hypothesis", code, position),
      });
      h += 1;
    } else {
      throw new Error(
        `alignment code '${code}': unknown operation '${op}' at position ` +
          `${position}; the alphabet is ${alphabetLegend(alphabet)}`,
      );
    }
  }

  if (r !== reference.length || h !== hypothesis.length) {
    throw new Error(
      `alignment code '${code}' does not consume every token: ` +
        `${reference.length - r} of ${reference.length} reference tokens and ` +
        `${hypothesis.length - h} of ${hypothesis.length} hypothesis tokens ` +
        "left over",
    );
  }

  return parts;
}

/** The counts a run reports, read off the same parts the diff renders. */
export function alignmentCounts(parts: DiffPart[]): AlignmentCounts {
  let substitutions = 0;
  let deletions = 0;
  let insertions = 0;
  for (const part of parts) {
    if (part.kind === "substitute") substitutions += 1;
    else if (part.kind === "delete") deletions += 1;
    else if (part.kind === "insert") insertions += 1;
  }
  return {
    substitutions,
    deletions,
    insertions,
    errors: substitutions + deletions + insertions,
  };
}
