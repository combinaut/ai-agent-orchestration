// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import GoToForm from "#popup/components/GoToForm.vue";
import GroupList from "#popup/components/GroupList.vue";
import PrInput from "#popup/components/PrInput.vue";
import RepoSelect from "#popup/components/RepoSelect.vue";
import { createI18n } from "#test/i18n/catalog";
import { fakePanes } from "./fakePanes";
import { mountPopup } from "./mountPopup";

afterEach(() => {
  vi.unstubAllGlobals();
});

function speakGerman(): void {
  vi.stubGlobal("chrome", { i18n: createI18n("de") });
}

test("the form label comes from chrome.i18n", () => {
  speakGerman();

  const wrapper = mountPopup(GoToForm, {
    props: { busy: false, repos: [], showRepo: false, input: "", repo: "" },
  });

  assert.equal(wrapper.find("label").text(), "Zu einem PR gehen");
});

test("the input placeholder and the Enter hint come from chrome.i18n", () => {
  speakGerman();

  const wrapper = mountPopup(PrInput, { props: { readonly: false, modelValue: "76" } });

  assert.equal(wrapper.find("input").attributes("placeholder"), "2972 oder eine PR-URL");
  assert.equal(wrapper.find("kbd").attributes("aria-label"), "Eingabetaste drücken");
});

test("the repo dropdown label comes from chrome.i18n", () => {
  speakGerman();

  const wrapper = mountPopup(RepoSelect, { props: { repos: ["a", "b"], modelValue: "a" } });

  assert.equal(
    wrapper.find("[aria-label]").attributes("aria-label"),
    "Repository für eine einzelne Nummer",
  );
});

test("the dropdown live-region messages come from chrome.i18n", async () => {
  speakGerman();

  const wrapper = mountPopup(RepoSelect, { props: { repos: ["a", "b"], modelValue: "a" } });

  await wrapper.find(".p-select").trigger("click");

  const regions = [...document.body.querySelectorAll("[role=status]")];

  assert.deepEqual(
    regions.map((region) => region.textContent?.trim()),
    ["1 Repository ausgewählt"],
  );

  wrapper.unmount();
});

test("the group list label and the count label come from chrome.i18n", () => {
  speakGerman();

  const list = mountPopup(GroupList, {
    props: { groups: [{ title: "Finder", count: 1, prs: [] }], panes: fakePanes() },
  });

  assert.equal(list.attributes("aria-label"), "PR-Tab-Gruppen");
  assert.equal(list.find(".p-badge").attributes("aria-label"), "1 PR");
});
