// @vitest-environment happy-dom
import assert from "node:assert/strict";
import Button from "primevue/button";
import Tag from "primevue/tag";
import { test } from "vitest";
import PrItem from "#popup/components/PrItem.vue";
import type { PaneLookup, PrRow } from "#/types";
import { fakePanes } from "./fakePanes";
import { mountPopup } from "./mountPopup";

const ROW: PrRow = {
  tabId: 7,
  key: "acme/webapp#7",
  label: "webapp#7",
  title: "Fix the thing",
  head: { repo: "acme/webapp", branch: "feature" },
};

function item(
  answer: PaneLookup,
  checking = false,
  { row = ROW, selected = null }: { row?: PrRow; selected?: string | null } = {},
) {
  const panes = fakePanes(answer, checking);

  return { wrapper: mountPopup(PrItem, { props: { pr: row, panes, selected } }), panes };
}

test("a found pane without an agent state still enables focus", async () => {
  const { wrapper, panes } = item({ "acme/webapp:feature": { state: null } });
  const button = wrapper.findComponent(Button);

  assert.match(wrapper.text(), /#7\s*Fix the thing/);
  assert.equal(wrapper.text().includes("webapp"), false);
  assert.equal(button.find("button").element.disabled, false);
  assert.equal(button.attributes("aria-label"), "Go to the Herdr pane for webapp#7");

  await button.trigger("click");

  assert.deepEqual(panes.focused, [ROW.head]);
});

test("missing panes hide the button; checking keeps the loading spinner", () => {
  const missing = item({ "acme/webapp:feature": null }).wrapper;

  assert.equal(missing.find("[data-pr-pane]").exists(), false);
  assert.equal(item({}).wrapper.find("[data-pr-pane]").exists(), false);
  assert.equal(
    item({}, false, { row: { ...ROW, head: null } })
      .wrapper.find("[data-pr-pane]")
      .exists(),
    false,
  );

  const checking = item({}, true).wrapper.findComponent(Button);

  assert.equal(checking.props("loading"), true);
  assert.equal(checking.find("button").element.disabled, true);
});

test("the agent state shows as a tag with an aria label; none without an agent", () => {
  for (const [state, severity] of [
    ["working", "info"],
    ["idle", "secondary"],
    ["done", "success"],
    ["waiting", "warn"],
  ] as const) {
    const tag = item({ "acme/webapp:feature": { state } }).wrapper.findComponent(Tag);

    assert.equal(tag.props("value"), state);
    assert.equal(tag.props("severity"), severity);
    assert.equal(tag.attributes("aria-label"), `Agent ${state}`);
  }

  assert.equal(
    item({ "acme/webapp:feature": { state: null } })
      .wrapper.findComponent(Tag)
      .exists(),
    false,
  );
});

test("a row is an option with a stable id, selected only when its id is the active one", async () => {
  const { wrapper } = item({});
  const row = wrapper.find("li");

  assert.equal(row.attributes("id"), "pr-7");
  assert.equal(row.attributes("role"), "option");
  assert.equal(row.attributes("aria-selected"), "false");
  assert.equal(wrapper.find(".pr-key-hint").exists(), false);

  await wrapper.setProps({ selected: "pr-7" });

  assert.equal(row.attributes("aria-selected"), "true");
  assert.ok(row.classes().includes("pr-row-selected"));
  assert.equal(wrapper.find(".pr-key-hint").text(), "↵");
  assert.equal(
    wrapper.find(".pr-key-hint").element.nextElementSibling,
    wrapper.find("button[data-pr-close]").element,
  );
});

test("the go-to-pane icon sits in the small button target", () => {
  const button = item({ "acme/webapp:feature": { state: "working" } }).wrapper.findComponent(
    Button,
  );

  assert.equal(button.props("size"), "small");
  assert.equal(button.find(".pr-pane-icon").exists(), true);
});

test("Delete or Backspace on a focused row closes its tab", async () => {
  const { wrapper } = item({});
  const row = wrapper.find("button[data-pr-tab]");

  await row.trigger("keydown", { key: "Delete" });
  await row.trigger("keydown", { key: "Backspace" });
  await row.trigger("keydown", { key: "a" });

  assert.deepEqual(wrapper.emitted("close"), [
    [ROW.key, ROW.tabId],
    [ROW.key, ROW.tabId],
  ]);
});

test("a named native row button selects its PR; terminal focus remains separate", async () => {
  const { wrapper, panes } = item({ "acme/webapp:feature": { state: "working" } });
  const row = wrapper.find("button[data-pr-tab]");

  assert.equal(row.attributes("type"), "button");
  assert.equal(row.attributes("aria-label"), "webapp#7 Fix the thing");
  await row.trigger("click");
  assert.deepEqual(wrapper.emitted("go"), [[ROW.key]]);
  await wrapper.findComponent(Button).trigger("click");
  assert.deepEqual(panes.focused, [ROW.head]);
  assert.equal(wrapper.emitted("go")?.length, 1);
});

test("close is a named sibling control after the terminal and never selects the row", async () => {
  const { wrapper, panes } = item({ "acme/webapp:feature": { state: "working" } });
  const close = wrapper.find("button[data-pr-close]");
  let bubbled = 0;
  function handleBubble(): void {
    bubbled++;
  }
  wrapper.find("li").element.addEventListener("click", handleBubble);
  try {
    assert.equal(close.attributes("aria-label"), "Close tab #7");
    assert.equal(close.attributes("title"), "Close tab #7");
    assert.ok(close.find(".pi-times").exists());
    assert.equal(close.element.parentElement, wrapper.find("li").element);
    await close.trigger("click");
    assert.deepEqual(wrapper.emitted("close"), [[ROW.key, ROW.tabId]]);
    assert.equal(wrapper.emitted("go"), undefined);
    assert.equal(panes.focused.length, 0);
    assert.equal(bubbled, 0);
  } finally {
    wrapper.unmount();
  }
});

test("rows mark matched number and title ranges and render titles as text", async () => {
  const pr = {
    ...ROW,
    title: "<img> Fix",
    highlights: { number: [[1, 1]] as const, title: [[6, 8]] as const },
  };
  const { wrapper } = item({}, false, { row: pr });
  try {
    assert.deepEqual(
      wrapper.findAll("mark").map((mark) => mark.text()),
      ["7", "Fix"],
    );
    assert.equal(wrapper.find(".pr-number").text(), "#7");
    assert.equal(wrapper.find(".pr-title").text(), "<img> Fix");
    assert.equal(wrapper.find("img").exists(), false);
    await wrapper.setProps({ pr: ROW });
    assert.equal(wrapper.find("mark").exists(), false);
  } finally {
    wrapper.unmount();
  }
});
