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

test("any letter-bearing string is user text; ids, classes, urls and symbols are not", () => {
  for (const text of [
    "Go to a PR",
    "Untitled",
    "Opening…",
    "Closed 2 tabs.",
    "search",
    "Go",
    "OK",
  ]) {
    assert.equal(isUserText(text), true, text);
  }

  for (const text of ["goToLabel", "bg-group-grey", "https://github.com/*/*", "~", "", "+3"]) {
    assert.equal(isUserText(text), false, text);
  }
});

test("a code word is not text only in the files that use it as a code", () => {
  const sample = `const kind = "search";`;

  assert.deepEqual(literalProblems("src/background.ts", sample), []);
  assert.deepEqual(
    literalProblems(
      "src/popup/components/PrInput.vue",
      `<script setup lang="ts">${sample}</script>`,
    ),
    ['"search"'],
  );
  assert.deepEqual(literalProblems("src/texts.ts", sample), ['"search"']);
});

test("the check catches short strings: bound literals and named script values", () => {
  const named = `<script setup lang="ts">
const inputTitle = "search";
</script>
<template><input :title="inputTitle" :label="'Go'" /></template>`;

  assert.deepEqual(literalProblems("Sample.vue", named), ['"Go"', '"search"']);
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

test("the check catches short static labels that the prose rule would pass", () => {
  const vue = `<template><button>OK</button><span>Go</span><input placeholder="search" /></template>`;
  const html = `<html><body><button>OK</button><input title="go" /></body></html>`;

  assert.deepEqual(literalProblems("Sample.vue", vue), ['"OK"', '"Go"', '"search"']);
  assert.deepEqual(literalProblems("popup.html", html), ['"OK"', '"go"']);
});

test("the check catches text inside conditional and call expressions", () => {
  const sample = `<template>
  <Button :label="busy ? 'Opening…' : 'Open PRs'" :class="busy ? 'a b' : 'c d'" />
  <p>{{ busy ? "Please wait" : text }} {{ join(["one", "two words"]) }}</p>
</template>`;

  assert.deepEqual(literalProblems("Sample.vue", sample), [
    '"Opening…"',
    '"Open PRs"',
    '"Please wait"',
    '"one"',
    '"two words"',
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
    '"tabs"',
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
