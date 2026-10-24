// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { test } from "vitest";
import GroupItem from "#popup/components/GroupItem.vue";
import GroupList from "#popup/components/GroupList.vue";
import { mountPopup } from "./mountPopup";

test("one row per group, in the given order", () => {
  const groups = [
    { title: "Finder", color: "yellow", count: 2 },
    { title: "webapp", count: 1 },
  ];

  const wrapper = mountPopup(GroupList, { props: { groups } });
  const rows = wrapper.findAllComponents(GroupItem);

  assert.deepEqual(
    rows.map((row) => row.props("group")),
    groups,
  );
  assert.equal(mountPopup(GroupList, { props: { groups: [] } }).findAll("li").length, 0);
});
