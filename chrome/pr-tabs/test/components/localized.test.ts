// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import GroupList from "#popup/components/GroupList.vue";
import PopupField from "#popup/components/PopupField.vue";
import RepoOptionItem from "#popup/components/RepoOptionItem.vue";
import { createI18n } from "#test/i18n/catalog";
import { fakePanes } from "./fakePanes";
import { mountPopup } from "./mountPopup";

afterEach(() => {
  vi.unstubAllGlobals();
});

function speakGerman(): void {
  vi.stubGlobal("chrome", { i18n: createI18n("de") });
}

function field(mode: "idle" | "goto" | "search", matches = 0) {
  return mountPopup(PopupField, {
    props: { mode, matches, busy: false, expanded: false, active: null, modelValue: "" },
  });
}

test("the field placeholder and trailing hints come from chrome.i18n", () => {
  speakGerman();

  assert.equal(
    field("idle").find("input").attributes("placeholder"),
    "PR-Nummer, Link oder offene Tabs durchsuchen",
  );
  assert.equal(field("goto").find(".popup-field-trailing").text(), "PR-Nummer");
  assert.equal(field("search", 6).find(".popup-field-trailing").text(), "6 Treffer");
});

test("the already-open hint comes from chrome.i18n", () => {
  speakGerman();

  const option = mountPopup(RepoOptionItem, {
    props: {
      option: { id: "repo-a", repo: "a", color: "blue", open: "acme/a#1" },
      selected: null,
    },
  });

  assert.equal(option.find(".repo-option-hint").text(), "bereits offen · ↵ wechseln");
});

test("the group list label and the count label come from chrome.i18n", () => {
  speakGerman();

  const list = mountPopup(GroupList, {
    props: {
      groups: [{ id: "1", title: "Finder", count: 1, prs: [] }],
      panes: fakePanes(),
      collapsed: new Set<string>(),
      selected: null,
    },
  });

  assert.equal(list.attributes("aria-label"), "PR-Tab-Gruppen");
  assert.equal(list.find(".p-badge").attributes("aria-label"), "1 PR");
});
