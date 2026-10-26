// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises } from "@vue/test-utils";
import { test } from "vitest";
import { usePanes } from "#/composables/usePanes";
import { useStatus } from "#/composables/useStatus";
import { fakeAsk } from "#test/composables/fakeAsk";
import GroupItem from "#popup/components/GroupItem.vue";
import GroupList from "#popup/components/GroupList.vue";
import type { FakeReplies } from "#test/types";
import type { GroupSummary } from "#/types";
import { fakePanes } from "./fakePanes";
import { mountPopup } from "./mountPopup";

const HEAD = { repo: "acme/webapp", branch: "feature" };
const groups: GroupSummary[] = [
  {
    id: "1",
    title: "Finder",
    color: "yellow",
    count: 1,
    prs: [{ key: "acme/webapp#1", label: "webapp#1", title: "One", head: HEAD }],
  },
  {
    id: "2",
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
    wrapper
      .findAll<HTMLButtonElement>("button[aria-expanded]")
      .map((header) => header.attributes("aria-expanded")),
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

test("expanded groups resolve later batch heads with the real pane cache", async () => {
  const replies: FakeReplies = { panes: { "acme/webapp:feature": { state: "idle" } } };
  const fake = fakeAsk(replies);
  const panes = usePanes(fake.ask, useStatus());
  const first = groups[0];
  assert.ok(first);
  const wrapper = mountPopup(GroupList, { props: { groups: [first], panes } });

  try {
    await wrapper.find("button[aria-expanded]").trigger("click");
    await flushPromises();
    const later = { repo: "acme/webapp", branch: "later" };
    const updated: GroupSummary = {
      ...first,
      count: 2,
      prs: [...first.prs, { key: "acme/webapp#3", label: "webapp#3", title: "Later", head: later }],
    };
    replies.panes = { "acme/webapp:later": { state: "working" } };
    await wrapper.setProps({ groups: [updated] });
    await flushPromises();
    assert.deepEqual(fake.sent, [
      { type: "panes", heads: [HEAD] },
      { type: "panes", heads: [later] },
    ]);
    assert.equal(wrapper.findAll("li [data-pr-pane]").length, 2);
    assert.ok(wrapper.text().includes("working"));
  } finally {
    wrapper.unmount();
  }
});

test("a live count reorder preserves expansion and focused header identity", async () => {
  const finder = { id: "10", title: "Finder", count: 2, prs: [] };
  const webapp = { id: "9", title: "webapp", count: 1, prs: [] };
  const wrapper = mountPopup(GroupList, {
    props: { groups: [finder, webapp], panes: fakePanes() },
    attachTo: document.body,
  });
  try {
    const headers = wrapper.findAll<HTMLButtonElement>("button[aria-expanded]");
    assert.ok(headers[0]?.text().includes("Finder"));
    await headers[0]?.trigger("click");
    headers[0]?.element.focus();
    await wrapper.setProps({
      groups: [
        { ...webapp, count: 5 },
        { ...finder, count: 3 },
      ],
    });
    const reordered = wrapper.findAll<HTMLButtonElement>("button[aria-expanded]");
    assert.ok(reordered[0]?.text().includes("webapp"));
    assert.equal(reordered[1]?.attributes("aria-expanded"), "true");
    assert.ok(document.activeElement?.textContent?.includes("Finder"));
  } finally {
    wrapper.unmount();
  }
});

test("PR row focus stays on its key across a live row reorder", async () => {
  const first = { key: "acme/webapp#1" as const, label: "webapp#1", title: "First", head: null };
  const second = { key: "acme/webapp#2" as const, label: "webapp#2", title: "Second", head: null };
  const group = { id: "10", title: "Finder", count: 2, prs: [first, second] };
  const wrapper = mountPopup(GroupList, {
    props: { groups: [group], panes: fakePanes() },
    attachTo: document.body,
  });
  try {
    await wrapper.find("button[aria-expanded]").trigger("click");
    wrapper.find<HTMLButtonElement>("button[data-pr-tab]").element.focus();
    await wrapper.setProps({ groups: [{ ...group, prs: [second, first] }] });
    assert.equal(document.activeElement?.getAttribute("aria-label"), "webapp#1 First");
  } finally {
    wrapper.unmount();
  }
});
