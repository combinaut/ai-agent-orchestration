import assert from "node:assert/strict";
import { test } from "vitest";
import { generateThemeCss } from "#build/primevueTheme";
import { primeVueConfiguration } from "#popup/primevue";

const COMPONENTS = [
  "badge",
  "button",
  "iconfield",
  "inputicon",
  "inputtext",
  "message",
  "select",
  "virtualscroller",
];

test("build emits Aura styles for initial, conditional and overlay components", async () => {
  const css = await generateThemeCss();

  for (const component of COMPONENTS) {
    assert.ok(css.includes(`.p-${component}`), component);
  }

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
