// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { test } from "vitest";
import PopupField from "#popup/components/PopupField.vue";
import { mountPopup } from "./mountPopup";

function field(mode: "idle" | "goto" | "search", matches = 0) {
  return mountPopup(PopupField, {
    props: { mode, matches, busy: false, expanded: false, active: null, modelValue: "" },
  });
}

test("one field exposes the idle, go-to and search modes", async () => {
  const wrapper = field("idle");
  const input = wrapper.find<HTMLInputElement>("#target");

  assert.equal(input.attributes("role"), "combobox");
  assert.equal(input.attributes("aria-controls"), "popup-list");
  assert.equal(input.attributes("aria-expanded"), "false");
  assert.equal(input.attributes("aria-label"), "PR number, link, or search open tabs");
  assert.ok(wrapper.find(".pi-search").exists());
  assert.equal(wrapper.find(".popup-field-trailing").attributes("style"), "display: none;");
  assert.equal(wrapper.find(".popup-field-trailing").attributes("aria-live"), "polite");
  await wrapper.setProps({ mode: "goto", modelValue: "515" });
  assert.equal(wrapper.find(".popup-field-trailing").text(), "PR number");
  assert.ok(wrapper.find(".popup-field").classes().includes("popup-field-goto"));
  await wrapper.setProps({ mode: "search", matches: 3, modelValue: "amber" });
  assert.equal(wrapper.find(".popup-field-trailing").text(), "3 matches");
  await wrapper.setProps({ matches: 1 });
  assert.equal(wrapper.find(".popup-field-trailing").text(), "1 match");
  wrapper.unmount();
});

test("the active option id becomes aria-activedescendant", async () => {
  const wrapper = field("search");
  const input = wrapper.find("#target");

  assert.equal(input.attributes("aria-activedescendant"), undefined);
  await wrapper.setProps({ active: "pr-7", expanded: true });
  assert.equal(input.attributes("aria-activedescendant"), "pr-7");
  assert.equal(input.attributes("aria-expanded"), "true");
  wrapper.unmount();
});

test("Enter, arrows and Escape map to field commands; Escape on an empty field is left to Chrome", async () => {
  const wrapper = field("goto");
  const input = wrapper.find("#target");

  await input.trigger("keydown", { key: "Escape" });
  await input.trigger("keydown", { key: "Enter" });
  await input.trigger("keydown", { key: "ArrowDown" });
  await input.trigger("keydown", { key: "ArrowUp" });
  await input.trigger("keydown", { key: "Enter", isComposing: true });
  await wrapper.setProps({ modelValue: "515" });
  await input.trigger("keydown", { key: "Escape" });

  assert.equal(wrapper.emitted("go")?.length, 1);
  assert.equal(wrapper.emitted("next")?.length, 1);
  assert.equal(wrapper.emitted("previous")?.length, 1);
  assert.equal(wrapper.emitted("clear")?.length, 1);
  wrapper.unmount();
});
