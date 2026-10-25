import assert from "node:assert/strict";
import { test } from "vitest";
import type { Closed, Entry, Meta, Opened, States } from "#/types";
import { checkText } from "#/texts";
import { pruneMeta, recordStates, seedOpen, shouldClose, stillShowsPr, tabsToClose } from "#/core";

const background = { active: false };

test("a merged or closed PR closes its tab", () => {
  assert.equal(shouldClose(background, "MERGED"), true);
  assert.equal(shouldClose(background, "CLOSED"), true);
});

test("an open or unknown PR keeps its tab", () => {
  assert.equal(shouldClose(background, "OPEN"), false);
  assert.equal(shouldClose(background, undefined), false);
});

test("the tab in view stays until the user leaves it", () => {
  assert.equal(shouldClose({ active: true }, "MERGED"), false);
});

test("every background tab of a merged or closed PR is closed, nothing else", () => {
  const entries: Entry[] = [
    { tab: { id: 1, active: false }, pr: { key: "acme/webapp#1" } },
    { tab: { id: 2, active: false }, pr: { key: "acme/webapp#1" } },
    { tab: { id: 3, active: false }, pr: { key: "acme/webapp#2" } },
    { tab: { id: 4, active: false }, pr: { key: "acme/webapp#3" } },
    { tab: { id: 5, active: false }, pr: { key: "acme/webapp#4" } },
    { tab: { id: 6, active: true }, pr: { key: "acme/webapp#3" } },
  ];

  const states: States = {
    "acme/webapp#1": { state: "MERGED" },
    "acme/webapp#2": { state: "OPEN" },
    "acme/webapp#3": { state: "CLOSED" },
    "acme/webapp#4": null,
  };

  assert.deepEqual(tabsToClose(entries, states), [1, 2, 4]);
});

test("a tab that moved to another page since the poll started is not closed", () => {
  const key = "acme/webapp#1";

  assert.equal(
    stillShowsPr({ url: "https://github.com/acme/webapp/pull/1/files" }, key, "acme"),
    true,
  );
  assert.equal(stillShowsPr({ url: "https://github.com/acme/webapp/pull/2" }, key, "acme"), false);
  assert.equal(stillShowsPr({ url: "https://github.com/acme/webapp" }, key, "acme"), false);
  assert.equal(stillShowsPr(null, key, "acme"), false);
});

test("fetched states are recorded, missing PRs keep what was known", () => {
  const meta: Meta = { "acme/webapp#1": { state: "OPEN" }, "acme/webapp#3": { state: "OPEN" } };
  const states: States = {
    "acme/webapp#1": { state: "MERGED" },
    "acme/webapp#2": { state: "OPEN" },
    "acme/webapp#3": null,
  };

  assert.deepEqual(recordStates(meta, states), {
    "acme/webapp#1": { state: "MERGED" },
    "acme/webapp#2": { state: "OPEN" },
    "acme/webapp#3": { state: "OPEN" },
  });
});

test("opening a PR seeds OPEN only where no state is recorded yet", () => {
  const opened: Opened[] = [
    {
      hit: { title: "One", headRefName: "a", headRepository: { nameWithOwner: "fork/webapp" } },
      pr: { key: "acme/webapp#1" },
    },
    { hit: { title: "Two", headRefName: "b" }, pr: { key: "acme/webapp#2" } },
  ];

  const next = seedOpen(
    { "acme/webapp#2": { state: "CLOSED", title: "Two", headRefName: "b" } },
    opened,
  );

  assert.deepEqual(next, {
    "acme/webapp#1": {
      state: "OPEN",
      title: "One",
      headRefName: "a",
      headRepository: { nameWithOwner: "fork/webapp" },
    },
    "acme/webapp#2": { state: "CLOSED", title: "Two", headRefName: "b" },
  });
});

test("pruning keeps only PRs that still have a tab", () => {
  const meta: Meta = { "acme/webapp#1": { state: "OPEN" }, "acme/webapp#2": { state: "OPEN" } };

  assert.deepEqual(pruneMeta(meta, new Set(["acme/webapp#2"])), {
    "acme/webapp#2": { state: "OPEN" },
  });
});

test("the check result names what was closed, what stayed in view and what waits", () => {
  const closed: Closed[] = [
    { key: "acme/webapp#3273", state: "MERGED" },
    { key: "acme/webapp#3284", state: "MERGED" },
    { key: "acme/ai-agent-orchestration#17", state: "MERGED" },
  ];

  assert.equal(
    checkText({ tabs: 5, prs: 5, checked: 5, closed, inView: [] }),
    "Closed 3 merged tabs.",
  );
  assert.equal(
    checkText({
      tabs: 2,
      prs: 2,
      checked: 2,
      closed: [{ key: "acme/webapp#5", state: "CLOSED" }],
      inView: ["acme/webapp#9"],
    }),
    "Closed 1 closed-PR tab; 1 tab in view kept (acme/webapp#9).",
  );
  assert.equal(
    checkText({ tabs: 120, prs: 110, checked: 100, closed: [], inView: [] }),
    "No merged or closed PRs among 100 PRs checked; 10 more next time.",
  );
  assert.equal(
    checkText({ tabs: 0, prs: 0, checked: 0, closed: [], inView: [] }),
    "No PR tabs open",
  );
});
