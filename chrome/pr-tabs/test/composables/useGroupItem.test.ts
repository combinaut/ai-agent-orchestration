import assert from "node:assert/strict";
import { test } from "vitest";
import { useGroupItem } from "#/composables/useGroupItem";

test("a Chrome colour gets its dot; unknown and inherited names are grey", () => {
  const grey = useGroupItem({ title: "a", color: "grey", count: 1 }).dotClass.value;

  assert.equal(
    useGroupItem({ title: "a", color: "blue", count: 1 }).dotClass.value,
    "bg-group-blue",
  );

  for (const color of [undefined, "magenta", "constructor"]) {
    assert.equal(useGroupItem({ title: "a", color, count: 1 }).dotClass.value, grey, String(color));
  }
});

test("the count label names PRs, singular for one", () => {
  assert.equal(useGroupItem({ title: "a", count: 1 }).countLabel.value, "1 PR");
  assert.equal(useGroupItem({ title: "a", count: 4 }).countLabel.value, "4 PRs");
});
