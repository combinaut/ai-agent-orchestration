import assert from "node:assert/strict";
import { ref } from "vue";
import { test } from "vitest";
import { usePrPane } from "#/composables/usePrPane";
import type { AgentState, PaneLookup, PrRow } from "#/types";
import { fakePanes } from "#test/components/fakePanes";

const ROW: PrRow = {
  key: "acme/webapp#7",
  label: "webapp#7",
  title: "Fix",
  head: { repo: "Acme/WebApp", branch: "feature" },
};

function paneOf(answer: PaneLookup, checking = false, row: PrRow = ROW) {
  return usePrPane(row, fakePanes(answer, checking));
}

test("found, missing, checking and unchecked set the button", () => {
  const found = paneOf({ "acme/webapp:feature": { state: null } });

  assert.equal(found.availability.value, "found");
  assert.equal(found.isGoVisible.value, true);
  assert.equal(found.isGoDisabled.value, false);
  assert.equal(found.goLabel.value, "Go to the Herdr pane for webapp#7");

  const missing = paneOf({ "acme/webapp:feature": null });

  assert.equal(missing.availability.value, "missing");
  assert.equal(missing.isGoVisible.value, false);
  assert.equal(missing.isGoDisabled.value, true);
  assert.equal(missing.goLabel.value, "No Herdr pane has the branch of webapp#7 checked out");

  const checking = paneOf({}, true);

  assert.equal(checking.isChecking.value, true);
  assert.equal(checking.isGoVisible.value, true);
  assert.equal(checking.isGoDisabled.value, true);
  assert.equal(checking.goLabel.value, "Looking for the Herdr pane of webapp#7…");

  const unchecked = paneOf({});

  assert.equal(unchecked.isGoVisible.value, false);
  const noHead = paneOf({}, false, { ...ROW, head: null });

  assert.equal(noHead.availability.value, "missing");
  assert.equal(noHead.isGoVisible.value, false);
});

test("each agent state shows a tag with its text, label and severity", () => {
  const cases: [AgentState, string, string][] = [
    ["working", "working", "info"],
    ["idle", "idle", "secondary"],
    ["done", "done", "success"],
    ["waiting", "waiting", "warn"],
  ];

  for (const [state, text, severity] of cases) {
    const pane = paneOf({ "acme/webapp:feature": { state } });

    assert.equal(pane.isStateVisible.value, true, state);
    assert.equal(pane.stateText.value, text);
    assert.equal(pane.stateLabel.value, `Agent ${text}`);
    assert.equal(pane.stateSeverity.value, severity);
  }
});

test("no agent, an unknown state or no pane shows no tag", () => {
  for (const answer of [
    { "acme/webapp:feature": { state: null } },
    { "acme/webapp:feature": null },
    {},
  ]) {
    assert.equal(paneOf(answer).isStateVisible.value, false, JSON.stringify(answer));
  }
});

test("the row follows a changed lookup", () => {
  const panes = fakePanes({});
  const pane = usePrPane(ref(ROW), panes);

  panes.lookup.value = { "acme/webapp:feature": { state: "waiting" } };

  assert.equal(pane.availability.value, "found");
  assert.equal(pane.state.value, "waiting");
});

test("cached pane matches and misses take precedence over unrelated loading", () => {
  const found = paneOf({ "acme/webapp:feature": { state: "working" } }, true);
  const missing = paneOf({ "acme/webapp:feature": null }, true);

  assert.equal(found.availability.value, "found");
  assert.equal(found.isGoDisabled.value, false);
  assert.equal(found.isChecking.value, false);
  assert.equal(missing.availability.value, "missing");
  assert.equal(missing.isGoVisible.value, false);
  assert.equal(missing.isChecking.value, false);
});
