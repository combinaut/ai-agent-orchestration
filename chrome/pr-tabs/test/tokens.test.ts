import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "vitest";

const SRC = fileURLToPath(new URL("../src", import.meta.url));
const TOKENS = "popup/tokens.css";
const COLOUR = /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(/gi;
const ARBITRARY = /[\w:-]*-\[[^\]]*\]/g;
const LENGTH = /\b\d+(?:\.\d+)?(?:px|rem|em|vh|vw|%)(?![\w-])|\b(?:width|height)="\d/g;

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? sources(join(dir, entry.name)) : [join(dir, entry.name)],
  );
}

// Source text without comments, so prose like "#123" in a comment is not a colour.
function code(path: string): string {
  return readFileSync(path, "utf8")
    .replaceAll(/\/\*[\s\S]*?\*\//g, "")
    .replaceAll(/<!--[\s\S]*?-->/g, "")
    .replaceAll(/(^|\s)\/\/.*$/gm, "$1");
}

// Every finding of pattern in src/, outside the tokens file, as "file: match".
export function literals(pattern: RegExp, files = sources(SRC), only = /./): string[] {
  return files
    .filter((path) => relative(SRC, path) !== TOKENS && only.test(path))
    .flatMap((path) =>
      [...code(path).matchAll(pattern)].map((match) => `${relative(SRC, path)}: ${match[0]}`),
    );
}

test("no literal colour in src/ outside the tokens file", () => {
  assert.deepEqual(literals(COLOUR), []);
});

test("no Tailwind arbitrary values or literal lengths in the popup markup and styles", () => {
  assert.deepEqual(literals(ARBITRARY, undefined, /\.(?:vue|html|css)$|composables/), []);
  assert.deepEqual(literals(LENGTH, undefined, /\.(?:vue|html|css)$/), []);
});

test("the checks catch what they ban", () => {
  const sample =
    'class="bg-[#fff] w-[300px]" style="color: rgb(0 0 0)" width="16" :style="{ top: 3px }"';
  assert.deepEqual(
    [...sample.matchAll(COLOUR)].map((m) => m[0]),
    ["#fff", "rgb("],
  );
  assert.deepEqual(
    [...sample.matchAll(ARBITRARY)].map((m) => m[0]),
    ["bg-[#fff]", "w-[300px]"],
  );
  assert.deepEqual(
    [...sample.matchAll(LENGTH)].map((m) => m[0]),
    ["300px", 'width="1', "3px"],
  );
});

// The utility a @theme token makes, e.g. --text-body -> text-body, --color-popup-text -> text-popup-text.
function utilities(css: string): string[] {
  const prefixes: Record<string, string[]> = { color: ["bg", "text"], text: ["text"] };

  return [...css.matchAll(/--(color|text)-([\w-]+):/g)].flatMap(([, kind = "", name]) =>
    (prefixes[kind] ?? []).map((prefix) => `${prefix}-${name}`),
  );
}

function duplicates(names: string[]): string[] {
  return names.filter((name, index) => names.indexOf(name) !== index);
}

test("no two @theme tokens claim the same Tailwind utility", () => {
  const theme = readFileSync(join(SRC, "popup/tailwind.css"), "utf8");
  assert.deepEqual(duplicates(utilities(theme)), []);
  assert.deepEqual(duplicates(utilities("--color-popup: a; --text-popup: b;")), ["text-popup"]);
});
