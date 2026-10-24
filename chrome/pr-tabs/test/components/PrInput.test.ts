// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { nextTick } from "vue";
import { afterEach, test } from "vitest";
import PrInput from "#popup/components/PrInput.vue";
import { mountPopup } from "./mountPopup";

afterEach(() => {
  document.body.innerHTML = "";
});

test("the field is focused on open, shows the hint and updates its model", async () => {
  const wrapper = mountPopup(PrInput, {
    attachTo: document.body,
    props: { modelValue: "", readonly: false },
  });

  await nextTick();

  const field = wrapper.find("#target");

  assert.equal(document.activeElement, field.element);
  assert.equal(field.attributes("placeholder"), "2972 or a PR URL");
  assert.equal(wrapper.find("#target-hint").text(), "↵ Enter");

  await field.setValue("76");

  assert.deepEqual(wrapper.emitted("update:modelValue")?.at(-1), ["76"]);
});

test("a busy popup makes the field read-only", () => {
  const wrapper = mountPopup(PrInput, { props: { modelValue: "76", readonly: true } });
  assert.equal(wrapper.find("#target").attributes("readonly"), "");
});
