import assert from "node:assert/strict";
import { test } from "vitest";
import type { Owners, PrBranch } from "#/types";
import { groupForPr, allocateGroupColors } from "#/core";

const pr: PrBranch = { owner: "acme", repo: "webapp", number: 7, branch: "feature" };

test("owned branches group by architect; unknown branches use repository", () => {
  const owners: Owners = { "acme/webapp:feature": "Finder", "acme/docs:feature": "Tooling" };

  assert.equal(groupForPr(owners, pr).name, "Finder");
  assert.equal(groupForPr(owners, { ...pr, repo: "docs" }).name, "Tooling");
  assert.equal(groupForPr(owners, { ...pr, branch: "old" }).name, "webapp");
  assert.equal(groupForPr({}, pr).name, "webapp");
  assert.equal(
    groupForPr(owners, { ...pr, headRepository: { nameWithOwner: "someone/webapp" } }).name,
    "webapp",
  );
});

test("three formerly pink groups get distinct colours, retained across refreshes", () => {
  const names = ["group-2", "group-9", "group-10"];
  const colors = allocateGroupColors(names, {});
  assert.deepEqual(colors, { "group-10": "grey", "group-2": "blue", "group-9": "red" });
  assert.deepEqual(allocateGroupColors(names.toReversed(), colors), colors);
  assert.deepEqual(allocateGroupColors(["group-2", "group-9", "new"], colors), {
    "group-2": "blue",
    "group-9": "red",
    new: "grey",
  });
});

test("all nine colours are used before the least-used colour is reused", () => {
  const names = Array.from({ length: 20 }, (_, index) => `group-${String(index).padStart(2, "0")}`);
  const colors = allocateGroupColors(names, {});
  assert.equal(new Set(Object.values(allocateGroupColors(names.slice(0, 9), {}))).size, 9);
  const counts = Object.values(colors).reduce<Record<string, number>>((all, color) => {
    all[color] = (all[color] ?? 0) + 1;

    return all;
  }, {});
  assert.equal(Math.max(...Object.values(counts)), 3);
  assert.equal(Math.min(...Object.values(counts)), 2);
  assert.equal(allocateGroupColors(["old", "new"], { old: "orange" }).old, "orange");
});

test("an explicitly unavailable head repository never matches the base checkout", () => {
  assert.equal(
    groupForPr({ "acme/webapp:feature": "Finder" }, { ...pr, headRepository: null }).name,
    "webapp",
  );
});

test("PR status ownership applies after a branch ownership match", () => {
  const status = { "acme/webapp#7": "Watcher" };

  assert.equal(groupForPr(status, pr).name, "Watcher");
  assert.equal(groupForPr({ ...status, "acme/webapp:feature": "Finder" }, pr).name, "Finder");
});
