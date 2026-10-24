// @vitest-environment happy-dom
import assert from "node:assert/strict";
import Button from "primevue/button";
import { test } from "vitest";
import ActionButton from "#popup/components/ActionButton.vue";
import { mountPopup } from "./mountPopup";

test("a click runs the action, and a busy button is disabled", async () => {
  const wrapper = mountPopup(ActionButton, { props: { label: "Close merged PRs", busy: false } });

  assert.equal(wrapper.text(), "Close merged PRs");

  await wrapper.find("button").trigger("click");

  assert.equal(wrapper.emitted("run")?.length, 1);

  await wrapper.setProps({ busy: true });

  assert.equal(wrapper.find("button").attributes("disabled"), "");
});

test("the primary button has more space above and the default colour", () => {
  const primary = mountPopup(ActionButton, { props: { label: "a", busy: false, primary: true } });

  assert.ok(primary.classes().includes("mt-3"));
  assert.equal(primary.findComponent(Button).props("severity"), null);

  const secondary = mountPopup(ActionButton, { props: { label: "b", busy: false } });

  assert.ok(secondary.classes().includes("mt-2"));
  assert.equal(secondary.findComponent(Button).props("severity"), "secondary");
});
