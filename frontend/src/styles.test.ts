import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";

const css = readFileSync(join(process.cwd(), "src/styles.css"), "utf8");

function declarations(): [string, string][] {
  return [...css.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)].map((match) => [
    match[1],
    match[2],
  ]);
}

test("no custom property refers to itself", () => {
  // `--tint: var(--tint)` is invalid at computed-value time: every declaration
  // that uses it is dropped silently, which is how a theme ends up half
  // applied with nothing in the console to explain it.
  const broken = declarations()
    .filter(([name, value]) =>
      new RegExp(`var\\(\\s*${name}\\s*\\)`).test(value),
    )
    .map(([name]) => name);

  expect(broken).toEqual([]);
});

test("every token the stylesheet uses is defined somewhere", () => {
  const defined = new Set(declarations().map(([name]) => name));
  const used = new Set(
    [...css.matchAll(/var\(\s*(--[a-z0-9-]+)\s*[,)]/g)].map((match) => match[1]),
  );

  expect([...used].filter((name) => !defined.has(name))).toEqual([]);
});

test("both themes are real: same tokens, re-pointed", () => {
  const lightAt = css.indexOf(':root[data-theme="light"]');
  expect(lightAt).toBeGreaterThan(-1);
  const light = css.slice(lightAt, css.indexOf("\n}", lightAt));
  const dark = css.slice(css.indexOf(":root {"), css.indexOf("\n}", css.indexOf(":root {")));

  for (const token of ["--bg:", "--surface:", "--text:", "--border:", "--accent:"]) {
    expect(dark).toContain(token);
    expect(light).toContain(token);
  }
  expect(dark).toContain("color-scheme: dark");
  expect(light).toContain("color-scheme: light");
});
