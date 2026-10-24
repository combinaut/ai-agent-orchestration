import assert from "node:assert/strict";
import { test } from "vitest";
import { groupForPr, groupColor } from "#/core";

const pr = { owner: "acme", repo: "webapp", branch: "feature" };

test("owned branches group by architect; unknown branches use repository", () => {
  const owners = { "acme/webapp:feature": "Finder", "acme/docs:feature": "Tooling" };

  assert.equal(groupForPr(owners, pr).name, "Finder");
  assert.equal(groupForPr(owners, { ...pr, repo: "docs" }).name, "Tooling");
  assert.equal(groupForPr(owners, { ...pr, branch: "old" }).name, "webapp");
  assert.equal(groupForPr({}, pr).name, "webapp");
  assert.equal(
    groupForPr(owners, { ...pr, headRepository: { nameWithOwner: "someone/webapp" } }).name,
    "webapp",
  );
});

test("name hash produces stable Chrome colours independent of repository", () => {
  assert.equal(groupColor("Finder"), "yellow");

  const owners = { "acme/webapp:feature": "Finder", "acme/docs:feature": "Finder" };

  assert.equal(groupForPr(owners, pr).color, groupForPr(owners, { ...pr, repo: "docs" }).color);

  const colors = new Set([
    "grey",
    "blue",
    "red",
    "yellow",
    "green",
    "pink",
    "purple",
    "cyan",
    "orange",
  ]);

  for (const name of ["Finder", "Agent::Tooling", "webapp", "📚 Docs"]) {
    assert.ok(colors.has(groupColor(name)));
  }
});

test("an explicitly unavailable head repository never matches the base checkout", () => {
  assert.equal(
    groupForPr({ "acme/webapp:feature": "Finder" }, { ...pr, headRepository: null }).name,
    "webapp",
  );
});
