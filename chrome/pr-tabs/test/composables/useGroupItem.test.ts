import assert from "node:assert/strict";
import { test } from "vitest";
import { useGroupItem } from "#/composables/useGroupItem";
import type { GroupColor } from "#/types";

test("a Chrome colour gets its dot; unknown and inherited names are grey", () => {
  const grey = useGroupItem({ id: "1", title: "a", color: "grey", count: 1, prs: [] }).dotClass
    .value;

  assert.equal(
    useGroupItem({ id: "2", title: "a", color: "blue", count: 1, prs: [] }).dotClass.value,
    "bg-group-blue",
  );

  // Colours Chrome does not have, as an older or broken extension state could hold.
  const unknown = [undefined, "magenta", "constructor"] as (GroupColor | undefined)[];

  for (const color of unknown) {
    assert.equal(
      useGroupItem({ id: "3", title: "a", color, count: 1, prs: [] }).dotClass.value,
      grey,
      String(color),
    );
  }
});

test("the count label names PRs, singular for one", () => {
  assert.equal(useGroupItem({ id: "4", title: "a", count: 1, prs: [] }).countLabel.value, "1 PR");
  assert.equal(useGroupItem({ id: "5", title: "a", count: 4, prs: [] }).countLabel.value, "4 PRs");
});
