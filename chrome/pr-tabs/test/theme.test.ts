import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "vitest";
import { COMPONENTS, generateThemeCss } from "#build/primevueTheme";
import { primeVueConfiguration } from "#popup/primevue";

const SOURCE_ROOT = new URL("../src/", import.meta.url);
const PRIMEVUE_IMPORT = /\b(?:from|import)\s*["']primevue\/([^/"']+)(?:\/[^"']*)?["']/g;

function importedComponents(path: string): string[] {
  const source = readFileSync(new URL(path, SOURCE_ROOT), "utf8");
  const imports = Array.from(source.matchAll(PRIMEVUE_IMPORT), (match) => match[1] ?? "");

  return imports.filter((component) => component !== "config");
}

test("every source PrimeVue component has build-time styles registered", () => {
  const files = readdirSync(SOURCE_ROOT, { recursive: true, encoding: "utf8" });
  const components = files.filter((file) => /\.(?:vue|ts)$/.test(file)).flatMap(importedComponents);

  for (const component of components) {
    assert.ok(COMPONENTS.includes(component), `Missing build-time styles for ${component}`);
  }
});

test("popup entry imports the generated theme stylesheet", () => {
  const source = readFileSync(new URL("popup/main.ts", SOURCE_ROOT), "utf8");

  assert.match(source, /\bimport\s*["']virtual:primevue-theme\.css["']/);
});

test("transitive ripple and virtualscroller styles stay registered", () => {
  assert.ok(COMPONENTS.includes("ripple"));
  assert.ok(COMPONENTS.includes("virtualscroller"));
});

test("build emits Aura styles for initial, conditional and overlay components", async () => {
  const css = await generateThemeCss();

  for (const component of COMPONENTS) {
    const selector = component === "ripple" ? ".p-ink" : `.p-${component}`;

    assert.ok(css.includes(selector), component);
  }

  assert.match(css, /li\.p-select-option[^{}]*\{\s*transition:\s*none/);
  assert.match(css, /--p-primary-color:/);
  assert.match(css, /--p-select-option-selected-background:/);
  assert.match(css, /prefers-color-scheme:\s*dark/);
  assert.match(css, /@layer primevue/);
  assert.match(css, /@layer theme,\s*base,\s*primevue,\s*components,\s*utilities;/);
  assert.ok(!css.includes("<style"));
  assert.ok(!css.includes("dt("));
});

test("CSS generation is deterministic across repeated builds", async () => {
  assert.equal(await generateThemeCss(), await generateThemeCss());
});

test("runtime keeps styled classes and skips theme generation", () => {
  assert.equal(primeVueConfiguration.theme, "none");
  assert.notEqual(primeVueConfiguration.unstyled, true);
});
