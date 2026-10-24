// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { nextTick } from "vue";
import { afterEach, test } from "vitest";
import PrInput from "#popup/components/PrInput.vue";
import { mountPopup } from "./mountPopup";

afterEach(() => {
  document.body.innerHTML = "";
});

test("the field is focused on open and updates its model", async () => {
  const wrapper = mountPopup(PrInput, {
    attachTo: document.body,
    props: { modelValue: "", readonly: false },
  });

  await nextTick();

  const field = wrapper.find("#target");

  assert.equal(document.activeElement, field.element);
  assert.equal(field.attributes("placeholder"), "2972 or a PR URL");

  await field.setValue("76");

  assert.deepEqual(wrapper.emitted("update:modelValue")?.at(-1), ["76"]);
});

test("the search icon and the Enter hint sit inside one field, the hint only with text", async () => {
  const wrapper = mountPopup(PrInput, { props: { modelValue: "", readonly: false } });
  const field = wrapper.find(".p-iconfield");

  assert.equal(wrapper.find(".p-inputgroup").exists(), false);
  assert.ok(field.find("svg").exists());
  assert.ok(field.find("#target").exists());
  assert.equal(wrapper.find("#target-hint").exists(), false);

  await wrapper.setProps({ modelValue: "  " });

  assert.equal(wrapper.find("#target-hint").exists(), false);

  await wrapper.setProps({ modelValue: "76" });

  assert.equal(field.find("#target-hint").element.textContent, "↵");
});

test("a busy popup makes the field read-only", () => {
  const wrapper = mountPopup(PrInput, { props: { modelValue: "76", readonly: true } });
  assert.equal(wrapper.find("#target").attributes("readonly"), "");
});
