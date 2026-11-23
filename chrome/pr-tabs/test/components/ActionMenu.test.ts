// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises } from "@vue/test-utils";
import { afterEach, test } from "vitest";
import ActionMenu from "#popup/components/ActionMenu.vue";
import type { ActionMenuItem } from "#/types";
import { mountPopup } from "./mountPopup";

afterEach(() => {
  document.body.replaceChildren();
});

const ACTIONS: ActionMenuItem[] = [
  { label: "Open all", icon: "pi-external-link", busy: false, run() {} },
  {
    label: "Close merged",
    icon: "pi-check-circle",
    busy: true,
    separated: true,
    run() {},
  },
];

function menu() {
  return mountPopup(ActionMenu, {
    attachTo: document.body,
    props: { label: "Actions", actions: ACTIONS },
  });
}

test("the real menu stays visible after opening and closes on the next click", async () => {
  const wrapper = menu();

  try {
    const trigger = wrapper.find("#actions-menu");
    assert.ok(trigger.find(".pi-bars").exists());
    await trigger.trigger("click");
    await flushPromises();

    assert.ok(document.getElementById("actions-menu-popup"));
    assert.ok(document.body.textContent?.includes("Open all"));
    assert.equal(trigger.attributes("aria-expanded"), "true");
    assert.ok(trigger.classes().includes("menu-button-open"));
    await trigger.trigger("click");
    await flushPromises();
    assert.equal(trigger.attributes("aria-expanded"), "false");
    assert.equal(trigger.classes().includes("menu-button-open"), false);
  } finally {
    wrapper.unmount();
  }
});

test("items show PrimeIcons, a separator before a separated item, and disabled busy items", async () => {
  const wrapper = menu();

  try {
    await wrapper.find("#actions-menu").trigger("click");
    await flushPromises();

    const popup = document.getElementById("actions-menu-popup");
    assert.ok(popup?.querySelector(".pi-external-link"));
    assert.ok(popup?.querySelector(".pi-check-circle"));
    assert.equal(popup?.querySelectorAll(".p-menu-separator").length, 1);
    assert.deepEqual(
      [...(popup?.querySelectorAll("a") ?? [])].map((link) => link.getAttribute("aria-disabled")),
      ["false", "true"],
    );
  } finally {
    wrapper.unmount();
  }
});
