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
import type { GroupSummary, Panes, PrRow } from "#/types";
import { fakePanes } from "./fakePanes";
import { mountPopup } from "./mountPopup";

const HEAD = { repo: "acme/webapp", branch: "feature" };
const LATER_HEAD = { repo: "acme/webapp", branch: "later" };
const LATER_ROW: PrRow = {
  tabId: 3,
  key: "acme/webapp#3",
  label: "webapp#3",
  title: "Later",
  head: LATER_HEAD,
};

const groups: GroupSummary[] = [
  {
    id: "1",
    title: "Finder",
    color: "yellow",
    count: 1,
    prs: [{ tabId: 1, key: "acme/webapp#1", label: "webapp#1", title: "One", head: HEAD }],
  },
  {
    id: "2",
    title: "webapp",
    count: 1,
    prs: [{ tabId: 2, key: "acme/webapp#2", label: "webapp#2", title: "", head: null }],
  },
];

function list(
  shown: GroupSummary[],
  {
    panes = fakePanes(),
    collapsed = new Set<string>(),
    selected = null,
  }: { panes?: Panes; collapsed?: ReadonlySet<string>; selected?: string | null } = {},
) {
  return mountPopup(GroupList, {
    props: { groups: shown, panes, collapsed, selected },
    attachTo: document.body,
  });
}

test("one open panel per group, in the given order", () => {
  const wrapper = list(groups);

  try {
    assert.deepEqual(
      wrapper.findAllComponents(GroupItem).map((row) => row.props("group")),
      groups,
    );
    assert.deepEqual(
      wrapper.findAll("button[aria-expanded]").map((header) => header.attributes("aria-expanded")),
      ["true", "true"],
    );
    assert.match(wrapper.text(), /#1\s*One/);
  } finally {
    wrapper.unmount();
  }
});

test("collapsed ids close their panels; header clicks ask to toggle", async () => {
  const wrapper = list(groups, { collapsed: new Set(["2"]) });

  try {
    const headers = wrapper.findAll("button[aria-expanded]");

    assert.deepEqual(
      headers.map((header) => header.attributes("aria-expanded")),
      ["true", "false"],
    );
    await headers[1]?.trigger("click");
    await wrapper.setProps({ collapsed: new Set() });
    await headers[0]?.trigger("click");
    assert.deepEqual(wrapper.emitted("collapse"), [
      ["2", false],
      ["1", true],
    ]);
  } finally {
    wrapper.unmount();
  }
});

test("the selected id marks its row", () => {
  const wrapper = list(groups, { selected: "pr-2" });

  try {
    assert.deepEqual(
      wrapper.findAll("li[role=option]").map((row) => row.attributes("aria-selected")),
      ["false", "true"],
    );
  } finally {
    wrapper.unmount();
  }
});

test("listed groups look up the panes of their PR heads", async () => {
  const panes = fakePanes();
  const wrapper = list(groups, { panes });

  try {
    await flushPromises();
    assert.deepEqual(panes.ensured, [[HEAD]]);
  } finally {
    wrapper.unmount();
  }
});

test("later batch heads resolve with the real pane cache", async () => {
  const replies: FakeReplies = { panes: { "acme/webapp:feature": { state: "idle" } } };
  const fake = fakeAsk(replies);
  const panes = usePanes(fake.ask, useStatus());
  const first = groups[0];
  assert.ok(first);
  const wrapper = list([first], { panes });

  try {
    await flushPromises();
    const updated: GroupSummary = { ...first, count: 2, prs: [...first.prs, LATER_ROW] };
    replies.panes = { "acme/webapp:later": { state: "working" } };
    await wrapper.setProps({ groups: [updated] });
    await flushPromises();
    assert.deepEqual(fake.sent, [
      { type: "panes", heads: [HEAD] },
      { type: "panes", heads: [LATER_HEAD] },
    ]);
    assert.equal(wrapper.findAll("li [data-pr-pane]").length, 2);
    assert.ok(wrapper.text().includes("working"));
  } finally {
    wrapper.unmount();
  }
});

test("a live count reorder keeps collapse state and the focused header", async () => {
  const finder = { id: "10", title: "Finder", count: 2, prs: [] };
  const webapp = { id: "9", title: "webapp", count: 1, prs: [] };
  const wrapper = list([finder, webapp], { collapsed: new Set(["9"]) });

  try {
    wrapper.findAll<HTMLButtonElement>("button[aria-expanded]")[0]?.element.focus();
    await wrapper.setProps({
      groups: [
        { ...webapp, count: 5 },
        { ...finder, count: 3 },
      ],
    });
    const reordered = wrapper.findAll("button[aria-expanded]");
    assert.ok(reordered[0]?.text().includes("webapp"));
    assert.equal(reordered[0]?.attributes("aria-expanded"), "false");
    assert.equal(reordered[1]?.attributes("aria-expanded"), "true");
    assert.ok(document.activeElement?.textContent?.includes("Finder"));
  } finally {
    wrapper.unmount();
  }
});

test("PR row focus stays on its key across a live row reorder", async () => {
  const first = {
    tabId: 1,
    key: "acme/webapp#1" as const,
    label: "webapp#1",
    title: "First",
    head: null,
  };
  const second = {
    tabId: 2,
    key: "acme/webapp#2" as const,
    label: "webapp#2",
    title: "Second",
    head: null,
  };
  const group = { id: "10", title: "Finder", count: 2, prs: [first, second] };
  const wrapper = list([group]);

  try {
    wrapper.find<HTMLButtonElement>("button[data-pr-tab]").element.focus();
    await wrapper.setProps({ groups: [{ ...group, prs: [second, first] }] });
    assert.equal(document.activeElement?.getAttribute("aria-label"), "webapp#1 First");
  } finally {
    wrapper.unmount();
  }
});
