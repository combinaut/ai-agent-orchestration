import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "vitest";

const SRC = fileURLToPath(new URL("../src", import.meta.url));
const TOKENS = "popup/tokens.css";
const COLOUR = /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(/gi;
const ARBITRARY = /[\w:-]*-\[[^\]]*\]/g;
const CSS_IN_MARKUP = /<style[^>]*>([\s\S]*?)<\/style>|\s(?::|v-bind:)?style="([^"]*)"/g;
const COLOUR_VALUE =
  /(?:^|[\s;{,])(--[\w-]*colou?r[\w-]*|[\w-]*colou?r|background|fill|stroke|border(?:-[a-z]+)*|outline|box-shadow|text-shadow)\s*:\s*['"]?([^;}'"]+)/gi;
const NOT_A_COLOUR = new Set([
  "transparent",
  "currentcolor",
  "inherit",
  "initial",
  "unset",
  "revert",
  "none",
  "auto",
  "solid",
  "dashed",
  "dotted",
  "double",
  "inset",
]);
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
function literals(pattern: RegExp, only = /./): string[] {
  return sources(SRC)
    .filter((path) => relative(SRC, path) !== TOKENS && only.test(path))
    .flatMap((path) =>
      [...code(path).matchAll(pattern)].map((match) => `${relative(SRC, path)}: ${match[0]}`),
    );
}

function stylesOf(path: string, text: string): string {
  if (path.endsWith(".css")) {
    return text;
  }

  return [...text.matchAll(CSS_IN_MARKUP)].map((match) => match[1] ?? match[2] ?? "").join(";");
}

function isNamedColour(word: string): boolean {
  return /^[a-z]+$/i.test(word) && !NOT_A_COLOUR.has(word.toLowerCase());
}

// Bare words in colour declarations, after var() and other functions are dropped.
function namedColours(styles: string): string[] {
  return [...styles.matchAll(COLOUR_VALUE)].flatMap((match) =>
    (match[2] ?? "")
      .replaceAll(/[\w-]*\([^)]*\)/g, " ")
      .split(/[\s,]+/)
      .filter(isNamedColour),
  );
}

function namedColourLiterals(): string[] {
  return sources(SRC)
    .filter((path) => relative(SRC, path) !== TOKENS && /\.(?:vue|html|css)$/.test(path))
    .flatMap((path) =>
      namedColours(stylesOf(path, code(path))).map((word) => `${relative(SRC, path)}: ${word}`),
    );
}

test("no literal colour in src/ outside the tokens file", () => {
  assert.deepEqual(literals(COLOUR), []);
  assert.deepEqual(namedColourLiterals(), []);
});

test("no Tailwind arbitrary values or literal lengths in the popup markup and styles", () => {
  assert.deepEqual(literals(ARBITRARY, /\.(?:vue|html|css)$|composables/), []);
  assert.deepEqual(literals(LENGTH, /\.(?:vue|html|css)$/), []);
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

  return [...css.matchAll(/--(color|text)-([\w-]+):/g)].flatMap((match) => {
    const [, kind = "", name] = match;

    return (prefixes[kind] ?? []).map((prefix) => `${prefix}-${name}`);
  });
}

function duplicates(names: string[]): string[] {
  return names.filter((name, index) => names.indexOf(name) !== index);
}

test("the named colour check reads styles, not other words", () => {
  const markup = `<p class="red" style="color: red">red</p>
    <i :style="{ borderColor: 'navy' }" />
    <style>a { border: 1px solid Green; color: var(--p-text-color); fill: currentColor; }</style>`;

  assert.deepEqual(namedColours(stylesOf("Sample.vue", markup)), ["red", "navy", "Green"]);
  assert.deepEqual(namedColours(stylesOf("t.css", "--color-x: var(--y); outline: none")), []);
});

test("no two @theme tokens claim the same Tailwind utility", () => {
  const theme = readFileSync(join(SRC, "popup/tailwind.css"), "utf8");
  assert.deepEqual(duplicates(utilities(theme)), []);
  assert.deepEqual(duplicates(utilities("--color-popup: a; --text-popup: b;")), ["text-popup"]);
});

test("the popup content owns a stable scrollbar gutter before groups expand", () => {
  const tailwind = readFileSync(join(SRC, "popup/tailwind.css"), "utf8");

  assert.match(tailwind, /@utility scrollbar-gutter-stable\s*\{\s*scrollbar-gutter: stable;/);
  assert.doesNotMatch(tailwind, /@layer base\s*\{\s*html\s*\{/);
});

test("PR rows use Aura's hover colour for pointer and keyboard interaction", () => {
  const tailwind = readFileSync(join(SRC, "popup/tailwind.css"), "utf8");

  assert.match(tailwind, /--color-row-hover: var\(--p-content-hover-background\);/);
  assert.match(tailwind, /--spacing-popup-content: var\(--pr-tabs-popup-max-height\);/);
  assert.match(tailwind, /--spacing-popup-padding: var\(--pr-tabs-popup-padding\);/);
  assert.match(tailwind, /--spacing-pane-icon: var\(--pr-tabs-pane-icon-size\);/);
});
