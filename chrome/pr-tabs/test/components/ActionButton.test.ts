// @vitest-environment happy-dom
import assert from "node:assert/strict";
import Button from "primevue/button";
import { test } from "vitest";
import ActionButton from "#popup/components/ActionButton.vue";
import { mountPopup } from "./mountPopup";

test("a click runs the action, and a busy button is disabled", async () => {
  const wrapper = mountPopup(ActionButton, {
    props: { label: "Close merged", hint: "Close merged PRs", busy: false },
  });

  assert.equal(wrapper.text(), "Close merged");
  assert.equal(wrapper.find("button").attributes("aria-label"), "Close merged PRs");
  assert.equal(wrapper.find("button").attributes("title"), "Close merged PRs");

  await wrapper.find("button").trigger("click");

  assert.equal(wrapper.emitted("run")?.length, 1);

  await wrapper.setProps({ busy: true });

  assert.equal(wrapper.find("button").attributes("disabled"), "");
});

test("the primary button has the default colour, the others are secondary", () => {
  const primary = mountPopup(ActionButton, {
    props: { label: "a", hint: "a", busy: false, primary: true },
  });

  assert.equal(primary.findComponent(Button).props("severity"), null);

  const secondary = mountPopup(ActionButton, { props: { label: "b", hint: "b", busy: false } });

  assert.equal(secondary.findComponent(Button).props("severity"), "secondary");
});

test("the button shares the row width with its siblings", () => {
  const wrapper = mountPopup(ActionButton, { props: { label: "a", hint: "a", busy: false } });

  assert.ok(wrapper.classes().includes("flex-1"));
});
