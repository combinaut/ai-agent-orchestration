// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises, mount } from "@vue/test-utils";
import PrimeVue from "primevue/config";
import { afterEach, test } from "vitest";
import ActionMenu from "#popup/components/ActionMenu.vue";
import { primeVueConfiguration } from "#popup/primevue";

afterEach(() => {
  document.body.replaceChildren();
});

test("the real menu stays visible after opening and closes on the next click", async () => {
  const wrapper = mount(ActionMenu, {
    attachTo: document.body,
    props: {
      label: "Actions",
      actions: [
        { id: "open", label: "Open all", hint: "", icon: "open" as const, busy: false, run() {} },
      ],
    },
    global: { plugins: [[PrimeVue, primeVueConfiguration]], stubs: { teleport: true } },
  });

  try {
    const trigger = wrapper.find("#actions-menu");
    assert.ok(trigger.classes().includes("size-6"));
    await trigger.trigger("click");
    await flushPromises();

    const menu = document.getElementById("actions-menu-popup");
    assert.ok(menu);
    assert.ok(document.body.textContent?.includes("Open all"));
    assert.equal(trigger.attributes("aria-expanded"), "true");
    await trigger.trigger("click");
    await flushPromises();
    assert.equal(trigger.attributes("aria-expanded"), "false");
  } finally {
    wrapper.unmount();
  }
});
