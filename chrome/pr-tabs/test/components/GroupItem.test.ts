// @vitest-environment happy-dom
import assert from "node:assert/strict";
import Badge from "primevue/badge";
import { test } from "vitest";
import GroupList from "#popup/components/GroupList.vue";
import type { GroupSummary } from "#/types";
import { fakePanes } from "./fakePanes";
import { mountPopup } from "./mountPopup";

function list(groups: GroupSummary[], collapsed: ReadonlySet<string> = new Set()) {
  return mountPopup(GroupList, {
    props: { groups, panes: fakePanes(), collapsed, selected: null },
  });
}

test("a header shows the colour rail class, name, count badge and an open chevron", () => {
  const wrapper = list([{ id: "1", title: "Finder", color: "yellow", count: 2, prs: [] }]);
  const badge = wrapper.findComponent(Badge);

  assert.ok(wrapper.find(".group-panel").classes().includes("group-yellow"));
  assert.equal(wrapper.find(".group-name").text(), "Finder");
  assert.equal(badge.props("value"), "2");
  assert.equal(badge.attributes("aria-label"), "2 PRs");
  assert.ok(wrapper.find(".group-chevron .pi-chevron-down").exists());
});

test("an unknown group colour falls back to the grey rail", () => {
  const wrapper = list([
    { id: "1", title: "Finder", color: "magenta" as "grey", count: 1, prs: [] },
  ]);

  assert.ok(wrapper.find(".group-panel").classes().includes("group-grey"));
});

test("a collapsed group shows a right chevron and keeps its badge", () => {
  const wrapper = list([{ id: "1", title: "Finder", count: 3, prs: [] }], new Set(["1"]));

  assert.ok(wrapper.find(".group-chevron .pi-chevron-right").exists());
  assert.equal(wrapper.find("button[aria-expanded]").attributes("aria-expanded"), "false");
  assert.equal(wrapper.findComponent(Badge).props("value"), "3");
});

test("a filtered group shows matches/total and marks the matched name", () => {
  const wrapper = list([
    { id: "1", title: "deploy-tools", count: 2, total: 5, titleMatches: [[0, 5]], prs: [] },
  ]);

  assert.equal(wrapper.findComponent(Badge).props("value"), "2/5");
  assert.equal(wrapper.find(".group-name mark").text(), "deploy");
});

test("← and → on a header ask to collapse and expand the group", async () => {
  const wrapper = list([{ id: "1", title: "Finder", count: 1, prs: [] }]);
  const header = wrapper.find("button[aria-expanded]");

  await header.trigger("keydown", { key: "ArrowLeft" });
  await header.trigger("keydown", { key: "ArrowRight" });
  await header.trigger("keydown", { key: "ArrowUp" });

  assert.deepEqual(wrapper.emitted("collapse"), [
    ["1", true],
    ["1", false],
  ]);
});
