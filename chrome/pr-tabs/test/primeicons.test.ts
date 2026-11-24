import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { test } from "vitest";

const ROOT = join(import.meta.dirname, "..");

function glyphs(css: string): Map<string, string> {
  const matches = css.matchAll(/\.(pi-[a-z-]+)::?before\s*\{\s*content:\s*"([^"]+)"/g);

  return new Map([...matches].map((match) => [match[1] ?? "", match[2] ?? ""]));
}

test("every PrimeIcon used by the extension has the pinned font glyph", () => {
  const src = join(ROOT, "src");
  const files = readdirSync(src, { recursive: true, encoding: "utf8" });
  const code = files
    .filter((file) => /\.(?:vue|ts)$/.test(file))
    .map((file) => readFileSync(join(src, file), "utf8"))
    .join("\n");
  const used = new Set(code.match(/\bpi-[a-z][a-z-]*/g));
  const local = glyphs(readFileSync(join(src, "popup/icons.css"), "utf8"));
  const pinned = glyphs(readFileSync(join(ROOT, "node_modules/primeicons/primeicons.css"), "utf8"));
  assert.ok(used.has("pi-cog"));
  assert.ok(used.has("pi-trash"));
  for (const icon of used) {
    assert.ok(local.has(icon), `${icon} has no local glyph rule`);
    assert.equal(local.get(icon), pinned.get(icon), `${icon} differs from the pinned font`);
  }
});
