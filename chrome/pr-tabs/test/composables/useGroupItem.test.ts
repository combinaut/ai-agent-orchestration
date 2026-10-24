import assert from "node:assert/strict";
import { test } from "vitest";
import { useGroupItem } from "#/composables/useGroupItem";
import type { GroupColor } from "#/types";

test("a Chrome colour gets its dot; unknown and inherited names are grey", () => {
  const grey = useGroupItem({ title: "a", color: "grey", count: 1 }).dotClass.value;

  assert.equal(
    useGroupItem({ title: "a", color: "blue", count: 1 }).dotClass.value,
    "bg-group-blue",
  );

  // Colours Chrome does not have, as an older or broken extension state could hold.
  const unknown = [undefined, "magenta", "constructor"] as (GroupColor | undefined)[];

  for (const color of unknown) {
    assert.equal(useGroupItem({ title: "a", color, count: 1 }).dotClass.value, grey, String(color));
  }
});

test("the count label names PRs, singular for one", () => {
  assert.equal(useGroupItem({ title: "a", count: 1 }).countLabel.value, "1 PR");
  assert.equal(useGroupItem({ title: "a", count: 4 }).countLabel.value, "4 PRs");
});
