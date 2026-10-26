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
  key: "acme/webapp#7",
  label: "webapp#7",
  title: "Fix the thing",
  head: { repo: "acme/webapp", branch: "feature" },
};

function item(answer: PaneLookup, checking = false, row: PrRow = ROW) {
  const panes = fakePanes(answer, checking);

  return { wrapper: mountPopup(PrItem, { props: { pr: row, panes } }), panes };
}

test("a found pane without an agent state still enables focus", async () => {
  const { wrapper, panes } = item({ "acme/webapp:feature": { state: null } });
  const button = wrapper.findComponent(Button);

  assert.match(wrapper.text(), /webapp#7 Fix the thing/);
  assert.equal(button.find("button").element.disabled, false);
  assert.equal(button.attributes("aria-label"), "Go to the Herdr pane for webapp#7");

  await button.trigger("click");

  assert.deepEqual(panes.focused, [ROW.head]);
});

test("missing panes hide the button; checking keeps the loading spinner", () => {
  const missing = item({ "acme/webapp:feature": null }).wrapper;

  assert.equal(missing.findComponent(Button).exists(), false);
  assert.equal(item({}).wrapper.findComponent(Button).exists(), false);
  assert.equal(
    item({}, false, { ...ROW, head: null })
      .wrapper.findComponent(Button)
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

test("a PR row has an Aura hover background for pointer and keyboard interaction", () => {
  const row = item({}).wrapper.find("li");

  assert.equal(row.classes().includes("hover:bg-row-hover"), true);
  assert.equal(row.classes().includes("focus-within:bg-row-hover"), true);
  assert.equal(row.classes().includes("-mx-popup-padding"), true);
  assert.equal(row.classes().includes("px-popup-padding"), true);
});

test("the go-to-pane icon uses the Aura icon size inside the small button target", () => {
  const button = item({ "acme/webapp:feature": { state: "working" } }).wrapper.findComponent(
    Button,
  );

  assert.equal(button.props("size"), "small");
  assert.equal(button.find(".size-pane-icon").exists(), true);
});

test("every row has a shared minimum height and pointer cursor", () => {
  const row = item({}).wrapper.find("li");

  assert.equal(row.classes().includes("min-h-pr-row"), true);
  assert.equal(row.classes().includes("cursor-pointer"), true);
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
