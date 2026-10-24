// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { test } from "vitest";
import SearchIcon from "#popup/components/SearchIcon.vue";
import { mountPopup } from "./mountPopup";

test("the icon is decoration, hidden from screen readers", () => {
  const svg = mountPopup(SearchIcon).find("svg");
  assert.equal(svg.attributes("aria-hidden"), "true");
  assert.ok(svg.classes().includes("size-icon"));
});
