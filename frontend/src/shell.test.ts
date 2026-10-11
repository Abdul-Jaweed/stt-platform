/**
 * The HTML shell: favicon set, and a theme boot that runs before paint.
 *
 * These assertions read index.html directly — the shell is not a component,
 * so nothing else would catch a dropped link tag or a renamed storage key.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

import { THEME_STORAGE_KEY } from "./lib/theme";

// vitest runs with the frontend package as cwd; import.meta.url is not a file
// URL under the test transform.
const html = readFileSync(join(process.cwd(), "index.html"), "utf8");

describe("index.html", () => {
  test("declares the favicon set", () => {
    expect(html).toMatch(/<link rel="icon"[^>]*href="\/favicon\.svg"/);
    expect(html).toMatch(/<link rel="apple-touch-icon"[^>]*href="\/apple-touch-icon\.png"/);
    expect(html).toMatch(/rel="manifest"|rel="icon"[^>]*sizes="32x32"/);
  });

  test("sets the theme before first paint, so there is no flash", () => {
    expect(html).toContain(THEME_STORAGE_KEY);
    expect(html).toMatch(/documentElement\.dataset\.theme/);
    expect(html).toMatch(/colorScheme/);
  });

  test("tells the browser both theme colours", () => {
    expect(html).toMatch(/<meta name="theme-color"[^>]*media="\(prefers-color-scheme: light\)"/);
    expect(html).toMatch(/<meta name="theme-color"[^>]*media="\(prefers-color-scheme: dark\)"/);
  });

  test("carries a description and a language", () => {
    expect(html).toMatch(/<html lang="en"/);
    expect(html).toMatch(/<meta name="description" content="[^"]{20,}"/);
  });
});
