// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises, mount } from "@vue/test-utils";
import PrimeVue from "primevue/config";
import Select from "primevue/select";
import { afterEach, test, vi } from "vitest";
import { effectiveRepos, pickedRepo, showRepoPicker } from "#/core";
import App from "#popup/App.vue";
import { primeVueOptions } from "#popup/primevue";

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

// Mounts the popup against a service worker whose settings list repos.
async function popup(repos: string[]) {
  const rules = { org: "acme", defaultRepo: "", openAll: "", repos };
  const sent: { type: string; repo?: string }[] = [];

  vi.stubGlobal("chrome", {
    runtime: {
      sendMessage: async (message: { type: string; repo?: string }) => {
        sent.push(message);

        if (message.type === "repos") {
          return {
            repos: effectiveRepos(rules),
            selected: pickedRepo(rules),
            show: showRepoPicker(rules),
          };
        }

        if (message.type === "summary") {
          return { prs: 0, groups: [] };
        }

        return { error: "unexpected" };
      },
    },
  });

  const wrapper = mount(App, {
    attachTo: document.body,
    global: { plugins: [[PrimeVue, primeVueOptions]] },
  });

  await flushPromises();

  return { wrapper, sent };
}

test("the repo dropdown shows only with two or more repos", async () => {
  for (const repos of [[], ["webapp"], ["webapp", "Webapp"]]) {
    const { wrapper } = await popup(repos);

    await wrapper.find("#target").setValue("76");

    assert.equal(wrapper.findComponent(Select).exists(), false, JSON.stringify(repos));
    assert.equal(wrapper.text().includes("No PR tabs open"), true);

    wrapper.unmount();
  }

  const { wrapper } = await popup(["webapp", "api"]);

  await wrapper.find("#target").setValue("76");

  const select = wrapper.findComponent(Select);

  assert.equal(select.exists(), true);
  assert.deepEqual(select.props("options"), ["webapp", "api"]);
  assert.equal(select.props("modelValue"), "webapp");

  wrapper.unmount();
});

test("the repo dropdown shows only while the input is a bare PR number", async () => {
  const { wrapper } = await popup(["webapp", "api"]);
  const field = wrapper.find("#target");

  for (const [text, shown] of [
    ["", false],
    ["76", true],
    ["https://github.com/acme/webapp/pull/76", false],
    ["sparkle#76", false],
    ["#76", false],
    ["76a", false],
    [" 76 ", true],
  ] as const) {
    await field.setValue(text);
    assert.equal(wrapper.findComponent(Select).exists(), shown, JSON.stringify(text));
  }

  wrapper.unmount();
});
