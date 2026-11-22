// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { test } from "vitest";
import PopupField from "#popup/components/PopupField.vue";
import { mountPopup } from "./mountPopup";

function field(mode: "idle" | "goto" | "search", matches = 0) {
  return mountPopup(PopupField, {
    props: { mode, matches, busy: false, modelValue: "" },
  });
}

test("one field exposes the idle, go-to and search modes", async () => {
  const wrapper = field("idle");
  const input = wrapper.find<HTMLInputElement>("#target");

  assert.equal(input.attributes("role"), "combobox");
  assert.equal(input.attributes("aria-expanded"), "false");
  assert.equal(wrapper.text().includes("matches"), false);
  await wrapper.setProps({ mode: "goto", modelValue: "515" });
  assert.equal(wrapper.text().includes("PR number"), true);
  await wrapper.setProps({ mode: "search", matches: 3, modelValue: "amber" });
  assert.equal(wrapper.text().includes("3 matches"), true);
  wrapper.unmount();
});

test("Enter, arrows and Escape preserve the single-field command order", async () => {
  const wrapper = field("goto");
  const input = wrapper.find("#target");

  await input.trigger("keydown", { key: "Enter" });
  await input.trigger("keydown", { key: "ArrowDown" });
  await wrapper.setProps({ modelValue: "515" });
  await input.trigger("keydown", { key: "Escape" });

  assert.equal(wrapper.emitted("go")?.length, 1);
  assert.equal(wrapper.emitted("next")?.length, 1);
  assert.equal(wrapper.emitted("clear")?.length, 1);
  wrapper.unmount();
});
