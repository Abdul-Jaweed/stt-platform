/**
 * The frontend source files, for the convention tests that read the codebase
 * itself (icon language, stylesheet tokens). Tests are excluded: they are
 * allowed to name the glyphs the app must not use.
 */
import { readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = join(process.cwd(), "src");

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir).sort()) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      walk(path, out);
      continue;
    }
    if (!/\.(ts|tsx|css)$/.test(entry)) continue;
    if (/\.test\.(ts|tsx)$/.test(entry)) continue;
    out.push(relative(process.cwd(), path).replaceAll("\\", "/"));
  }
  return out;
}

export const SOURCE_FILES: string[] = walk(ROOT);
