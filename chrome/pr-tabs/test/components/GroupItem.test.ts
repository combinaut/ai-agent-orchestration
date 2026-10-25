// @vitest-environment happy-dom
import assert from "node:assert/strict";
import Badge from "primevue/badge";
import { test } from "vitest";
import GroupList from "#popup/components/GroupList.vue";
import type { GroupSummary } from "#/types";
import { fakePanes } from "./fakePanes";
import { mountPopup } from "./mountPopup";

test("a row shows the group's colour dot, title and PR count", () => {
  const groups: GroupSummary[] = [{ title: "Finder", color: "yellow", count: 2, prs: [] }];
  const wrapper = mountPopup(GroupList, { props: { groups, panes: fakePanes() } });

  assert.ok(wrapper.find("[aria-hidden=true]").classes().includes("bg-group-yellow"));
  assert.match(wrapper.text(), /Finder/);

  const badge = wrapper.findComponent(Badge);

  assert.equal(badge.props("value"), 2);
  assert.equal(badge.attributes("aria-label"), "2 PRs");
});
