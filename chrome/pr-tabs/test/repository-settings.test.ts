// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises } from "@vue/test-utils";
import { afterEach, test } from "vitest";
import { mountPopup as mount } from "#test/components/mountPopup";
import RepositoriesSection from "#/options/RepositoriesSection.vue";

const original = chrome;
afterEach(() => {
  globalThis.chrome = original;
});

function settingsHost() {
  let added: string[] = ["other/docs"];
  const sent: { type: string; repo?: string }[] = [];
  globalThis.chrome = {
    ...original,
    runtime: {
      sendMessage: async (message: { type: string; repo?: string }) => {
        sent.push(message);
        if (message.type === "addRepository") {
          added = [...added, message.repo ?? ""];
        }
        if (message.type === "removeRepository") {
          added = added.filter((repo) => repo !== message.repo);
        }

        return { configured: ["acme/webapp"], added };
      },
    },
  } as unknown as typeof chrome;

  return { sent, added: () => added };
}

test("Settings mark config repos and validate, add with Enter submission, and remove added repos", async () => {
  const { sent, added } = settingsHost();
  const wrapper = mount(RepositoriesSection);
  await flushPromises();
  assert.equal(wrapper.findAll("li").length, 2);
  assert.match(wrapper.findAll("li")[0]?.text() ?? "", /from config/);
  assert.equal(wrapper.findAll("li")[0]?.find("button").exists(), false);
  await wrapper.get("#repository").setValue("bad");
  await wrapper.get("form").trigger("submit");
  assert.match(wrapper.get("#repository-feedback").text(), /valid owner\/name/);
  await wrapper.get("#repository").setValue("ACME/WEBAPP");
  await wrapper.get("form").trigger("submit");
  assert.match(wrapper.get("#repository-feedback").text(), /already listed/);
  assert.equal(sent.length, 1);
  await wrapper.get("#repository").setValue(" other/api ");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  assert.equal(wrapper.findAll("li").length, 3);
  assert.equal(wrapper.get<HTMLInputElement>("#repository").element.value, "");
  await wrapper.get('[aria-label="Remove other/api"]').trigger("click");
  await flushPromises();
  assert.deepEqual(added(), ["other/docs"]);
  wrapper.unmount();
});

test("Settings keep a failed load disabled and surface failed writes without losing the draft", async () => {
  let loadingFails = true;
  globalThis.chrome = {
    ...original,
    runtime: {
      sendMessage: async (message: { type: string }) =>
        loadingFails || message.type !== "repositorySettings"
          ? { error: "Unavailable" }
          : { configured: [], added: [] },
    },
  } as unknown as typeof chrome;
  const failed = mount(RepositoriesSection);
  await flushPromises();
  assert.equal(failed.get("fieldset").attributes("disabled"), "");
  assert.equal(failed.get("#repository-feedback").text(), "Unavailable");
  failed.unmount();
  loadingFails = false;
  const wrapper = mount(RepositoriesSection);
  await flushPromises();
  await wrapper.get("#repository").setValue("other/api");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  assert.equal(wrapper.get("#repository-feedback").text(), "Unavailable");
  assert.equal(wrapper.get<HTMLInputElement>("#repository").element.value, "other/api");
  assert.equal(wrapper.get("fieldset").attributes("disabled"), undefined);
  wrapper.unmount();
});

test("a successful removal retry clears the earlier failure message", async () => {
  let attempts = 0;
  globalThis.chrome = {
    ...original,
    runtime: {
      sendMessage: async (message: { type: string }) => {
        if (message.type === "repositorySettings") {
          return { configured: [], added: ["other/api"] };
        }
        attempts++;

        return attempts === 1 ? { error: "Unavailable" } : { configured: [], added: [] };
      },
    },
  } as unknown as typeof chrome;
  const wrapper = mount(RepositoriesSection);
  await flushPromises();
  await wrapper.get('[aria-label="Remove other/api"]').trigger("click");
  await flushPromises();
  assert.equal(wrapper.get("#repository-feedback").text(), "Unavailable");
  await wrapper.get('[aria-label="Remove other/api"]').trigger("click");
  await flushPromises();
  assert.equal(wrapper.findAll("li").length, 0);
  assert.equal(wrapper.get("#repository-feedback").text(), "");
  wrapper.unmount();
});
