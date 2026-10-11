/**
 * One icon language, enforced.
 *
 * The directive for this work is explicit: Phosphor Icons everywhere, no
 * emoji and no stray Unicode symbols standing in for UI icons. A convention
 * nobody can check is a convention that decays, so this test reads the source
 * and fails on the glyphs people reach for when they are in a hurry.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

import { SOURCE_FILES } from "./test/sources";

/** Ranges that only ever mean "someone used a character as an icon". */
const FORBIDDEN = [
  [0x1f300, 0x1faff, "emoji"],
  [0x2300, 0x23ff, "technical symbols"],
  [0x2600, 0x27bf, "dingbats / misc symbols"],
  [0x2190, 0x21ff, "arrows"],
  [0x25a0, 0x25ff, "geometric shapes"],
  [0x2900, 0x29ff, "misc arrows and math symbols"],
  [0x2b00, 0x2bff, "misc symbols and arrows"],
  [0xfe0f, 0xfe0f, "variation selector"],
] as const;

/** Legitimate typography — punctuation, not iconography. */
const ALLOWED = new Set(["·", "—", "–", "…", "’", "‘", "“", "”", "×", "°"]);

function offendersIn(text: string): string[] {
  const found: string[] = [];
  for (const char of text) {
    if (ALLOWED.has(char)) continue;
    const code = char.codePointAt(0) ?? 0;
    const range = FORBIDDEN.find(([low, high]) => code >= low && code <= high);
    if (range) {
      found.push(`${char} (U+${code.toString(16).toUpperCase()}, ${range[2]})`);
    }
  }
  return found;
}

describe("icon language", () => {
  test("no emoji or Unicode symbols stand in for icons", () => {
    const offenders: string[] = [];
    for (const file of SOURCE_FILES) {
      const text = readFileSync(join(process.cwd(), file), "utf8");
      for (const hit of offendersIn(text)) {
        offenders.push(`${file}: ${hit}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  test("Phosphor is the only icon library", () => {
    const packageJson = JSON.parse(
      readFileSync(join(process.cwd(), "package.json"), "utf8"),
    );
    const deps = {
      ...packageJson.dependencies,
      ...packageJson.devDependencies,
    } as Record<string, string>;
    const iconPackages = Object.keys(deps).filter((name) =>
      /(icon|icons)/i.test(name),
    );

    expect(iconPackages).toEqual(["@phosphor-icons/react"]);
  });

  test("icons are imported by name, so the bundle stays tree-shakeable", () => {
    const barrelImports: string[] = [];
    for (const file of SOURCE_FILES) {
      const text = readFileSync(join(process.cwd(), file), "utf8");
      // A namespace import would pull in every icon.
      if (/import\s+\*\s+as\s+\w+\s+from\s+"@phosphor-icons\/react"/.test(text)) {
        barrelImports.push(file);
      }
    }
    expect(barrelImports).toEqual([]);
  });
});
