// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises, mount } from "@vue/test-utils";
import PrimeVue from "primevue/config";
import { afterEach, test, vi } from "vitest";
import ActionMenu from "#popup/components/ActionMenu.vue";
import { primeVueConfiguration } from "#popup/primevue";

afterEach(() => {
  document.body.replaceChildren();
});

test("the real menu opens below its fixed-size trigger and runs an action", async () => {
  const run = vi.fn<() => void>();
  const wrapper = mount(ActionMenu, {
    attachTo: document.body,
    props: {
      label: "Actions",
      actions: [
        { id: "open", label: "Open all", hint: "", icon: "open" as const, busy: false, run },
      ],
    },
    global: { plugins: [[PrimeVue, primeVueConfiguration]] },
  });

  try {
    const trigger = wrapper.find("#actions-menu");
    assert.ok(trigger.element.parentElement?.classList.contains("size-6"));
    await trigger.trigger("click");
    await flushPromises();

    const menu = document.getElementById("actions-menu-popup");
    assert.equal(menu?.style.top, "calc(100% + 4px)");
    assert.equal(menu?.style.right, "0px");
    document.body.querySelector<HTMLElement>(".p-menu-item-link")?.click();
    assert.equal(run.mock.calls.length, 1);
  } finally {
    wrapper.unmount();
  }
});
