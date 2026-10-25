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

test("a found pane enables the button, which focuses the PR's head", async () => {
  const { wrapper, panes } = item({ "acme/webapp:feature": { state: "working" } });
  const button = wrapper.findComponent(Button);

  assert.match(wrapper.text(), /webapp#7 Fix the thing/);
  assert.equal(button.find("button").element.disabled, false);
  assert.equal(button.attributes("aria-label"), "Go to the Herdr pane for webapp#7");

  await button.trigger("click");

  assert.deepEqual(panes.focused, [ROW.head]);
});

test("no pane disables the button with the hint; checking shows it loading", () => {
  const missing = item({ "acme/webapp:feature": null }).wrapper;
  const disabled = missing.findComponent(Button);

  assert.equal(disabled.find("button").element.disabled, true);
  assert.equal(
    missing.find("[title]:not(.truncate)").attributes("title"),
    "No Herdr pane has the branch of webapp#7 checked out",
  );

  const checking = item({}, true).wrapper.findComponent(Button);

  assert.equal(checking.props("loading"), true);
  assert.equal(checking.find("button").element.disabled, true);
  assert.equal(item({}).wrapper.findComponent(Button).exists(), false);
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
