// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { test } from "vitest";
import RepoOptionItem from "#popup/components/RepoOptionItem.vue";
import type { RepoOption } from "#/types";
import { mountPopup } from "./mountPopup";

const OPTION: RepoOption = { id: "repo-web-app", repo: "web-app", color: "yellow", open: null };

function option(value: RepoOption = OPTION, selected: string | null = null) {
  return mountPopup(RepoOptionItem, { props: { option: value, selected } });
}

test("an option shows the group colour bar and the repo name, with no hint unless open", () => {
  const wrapper = option();

  assert.equal(wrapper.attributes("id"), "repo-web-app");
  assert.equal(wrapper.attributes("role"), "option");
  assert.ok(wrapper.find(".repo-option-bar").classes().includes("group-yellow"));
  assert.equal(wrapper.find(".repo-option-name").text(), "web-app");
  assert.equal(wrapper.find(".repo-option-hint").exists(), false);
});

test("an already open PR shows the switch hint; the selected option is marked", () => {
  const wrapper = option({ ...OPTION, open: "acme/web-app#1" }, "repo-web-app");

  assert.equal(wrapper.find(".repo-option-hint").text(), "already open · ↵ switch");
  assert.equal(wrapper.attributes("aria-selected"), "true");
  assert.ok(wrapper.classes().includes("repo-option-selected"));
});

test("click picks and hover reports the option", async () => {
  const wrapper = option();

  await wrapper.trigger("mouseenter");
  await wrapper.trigger("click");

  assert.deepEqual(wrapper.emitted("hover"), [[OPTION]]);
  assert.deepEqual(wrapper.emitted("pick"), [[OPTION]]);
});
