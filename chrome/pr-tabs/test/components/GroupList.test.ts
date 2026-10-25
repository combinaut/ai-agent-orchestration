// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises } from "@vue/test-utils";
import { test } from "vitest";
import GroupItem from "#popup/components/GroupItem.vue";
import GroupList from "#popup/components/GroupList.vue";
import type { GroupSummary } from "#/types";
import { fakePanes } from "./fakePanes";
import { mountPopup } from "./mountPopup";

const HEAD = { repo: "acme/webapp", branch: "feature" };
const groups: GroupSummary[] = [
  {
    title: "Finder",
    color: "yellow",
    count: 1,
    prs: [{ key: "acme/webapp#1", label: "webapp#1", title: "One", head: HEAD }],
  },
  {
    title: "webapp",
    count: 1,
    prs: [{ key: "acme/webapp#2", label: "webapp#2", title: "", head: null }],
  },
];

test("one collapsed panel per group, in the given order", () => {
  const wrapper = mountPopup(GroupList, { props: { groups, panes: fakePanes() } });
  const rows = wrapper.findAllComponents(GroupItem);

  assert.deepEqual(
    rows.map((row) => row.props("group")),
    groups,
  );
  assert.deepEqual(
    wrapper.findAll("button[aria-expanded]").map((header) => header.attributes("aria-expanded")),
    ["false", "false"],
  );
});

test("expanding a group lists its PRs and looks up the panes of every listed PR", async () => {
  const panes = fakePanes();
  const wrapper = mountPopup(GroupList, { props: { groups, panes }, attachTo: document.body });

  await wrapper.find("button[aria-expanded]").trigger("click");
  await flushPromises();

  assert.equal(wrapper.find("button[aria-expanded]").attributes("aria-expanded"), "true");
  assert.deepEqual(panes.ensured, [[HEAD]]);
  assert.match(wrapper.text(), /webapp#1 One/);

  wrapper.unmount();
});
