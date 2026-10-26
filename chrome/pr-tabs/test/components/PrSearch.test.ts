// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { test } from "vitest";
import PrSearch from "#popup/components/PrSearch.vue";
import { mountPopup } from "./mountPopup";

test("search has an accessible input, live count and keyboard commands", async () => {
  const wrapper = mountPopup(PrSearch, { props: { modelValue: "amber", active: true, count: 2 } });
  try {
    const input = wrapper.find("input");
    assert.equal(input.attributes("aria-label"), "Search PR tabs");
    assert.equal(input.attributes("autofocus"), undefined);
    assert.match(wrapper.find("[aria-live]").text(), /2 matching PRs/);
    await input.trigger("keydown", { key: "ArrowDown" });
    await input.trigger("keydown", { key: "Enter" });
    assert.equal(wrapper.emitted("next")?.length, 1);
    assert.equal(wrapper.emitted("go")?.length, 1);
    await input.trigger("keydown", { key: "Escape" });
    assert.deepEqual(wrapper.emitted("update:modelValue")?.at(-1), [""]);
    await wrapper.setProps({ modelValue: "", count: 0 });
    const escape = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    wrapper.find("input").element.dispatchEvent(escape);
    assert.equal(escape.defaultPrevented, false);
    await wrapper.setProps({ modelValue: "missing", count: 0 });
    assert.equal(wrapper.find("[aria-live]").text(), "No matching PRs");
  } finally {
    wrapper.unmount();
  }
});

test("search shortcuts leave IME candidate confirmation and navigation untouched", async () => {
  const wrapper = mountPopup(PrSearch, { props: { modelValue: "amber", active: true, count: 2 } });
  try {
    for (const key of ["Enter", "ArrowDown", "Escape"]) {
      const event = new KeyboardEvent("keydown", {
        key,
        isComposing: true,
        bubbles: true,
        cancelable: true,
      });
      wrapper.find("input").element.dispatchEvent(event);
      assert.equal(event.defaultPrevented, false);
    }
    assert.equal(wrapper.emitted("go"), undefined);
    assert.equal(wrapper.emitted("next"), undefined);
    assert.equal(wrapper.emitted("update:modelValue"), undefined);
  } finally {
    wrapper.unmount();
  }
});
