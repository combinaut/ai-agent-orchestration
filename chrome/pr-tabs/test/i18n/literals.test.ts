import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { test } from "vitest";
import { isUserText, literalProblems } from "./literals";

const SRC = join(import.meta.dirname, "../../src");
const SOURCE_FILE = /\.(?:ts|vue|html)$/;

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);

    if (entry.isDirectory()) {
      return sources(path);
    }

    return SOURCE_FILE.test(path) ? [path] : [];
  });
}

test("src holds no literal user-visible text", () => {
  const found = sources(SRC).flatMap((path) =>
    literalProblems(path, readFileSync(path, "utf8")).map(
      (problem) => `${relative(SRC, path)}: ${problem}`,
    ),
  );

  assert.deepEqual(found, []);
});

test("prose, a capitalised word and an ellipsis are user text; ids and classes are not", () => {
  for (const text of ["Go to a PR", "Untitled", "Opening…", "Closed 2 tabs."]) {
    assert.equal(isUserText(text), true, text);
  }

  for (const text of ["goToLabel", "MERGED", "bg-group-grey", "https://github.com/*/*", "~", ""]) {
    assert.equal(isUserText(text), false, text);
  }
});

test("the check catches text in a template: nodes, attributes, bound strings, interpolation", () => {
  const sample = `<template>
  <label for="x">Go to a PR</label>
  <input placeholder="Type here" :aria-label="'Search box'" class="w-full flex" />
  <p>{{ 'Nothing here' }} {{ title }}</p>
</template>`;

  assert.deepEqual(literalProblems("Sample.vue", sample), [
    '"Go to a PR"',
    '"Type here"',
    '"Search box"',
    '"Nothing here"',
  ]);
});

test("the check catches text in scripts and ignores logs, imports and type literals", () => {
  const sample = `import x from "primevue/some thing";
type Kind = "info message";
console.warn("PR tabs: using cache");
const a = "Opening…";
const b = \`Closed \${n} tabs\`;
throw new Error("Chrome created a tab without an id");`;

  assert.deepEqual(literalProblems("sample.ts", sample), [
    '"Opening…"',
    '"Closed"',
    '"Chrome created a tab without an id"',
  ]);
});

test("the check catches text in popup markup", () => {
  const sample = `<html><head><title>PR tabs</title></head>
<body><script>const a = "two words";</script><div aria-label="Some label">Hello there</div></body></html>`;

  assert.deepEqual(literalProblems("popup.html", sample), [
    '"PR tabs"',
    '"Hello there"',
    '"Some label"',
  ]);
});
