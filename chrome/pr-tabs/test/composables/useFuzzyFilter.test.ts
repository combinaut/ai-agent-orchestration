import assert from "node:assert/strict";
import { effectScope, nextTick, ref } from "vue";
import { test } from "vitest";
import type { GroupSummary } from "#/types";
import { useFuzzyFilter } from "#/composables/useFuzzyFilter";

const groups: GroupSummary[] = [
  {
    id: "10",
    title: "CobaltArchitect",
    count: 1,
    prs: [
      {
        tabId: 731,
        key: "acme/obsidian#731",
        label: "ShownLabel",
        title: "Amber Zeppelin",
        head: { repo: "acme/obsidian", branch: "velvetorbit" },
      },
    ],
  },
  {
    id: "9",
    title: "Different",
    count: 1,
    prs: [
      {
        tabId: 999,
        key: "acme/quartz#999",
        label: "quartz#999",
        title: "Plain record",
        head: null,
      },
    ],
  },
];

for (const query of [
  "AMBER ZEPELIN",
  "obsidian",
  "ShownLabel",
  "731",
  "#731",
  "velvetorbit",
  "CobaltArchitect",
]) {
  test(`Fuse indexes ${query} and hides nonmatching groups`, () => {
    const scope = effectScope();
    try {
      const filter = scope.run(() => useFuzzyFilter(ref(groups), ref(query)));
      assert.ok(filter);
      assert.deepEqual(
        filter.groups.value.map((group) => group.id),
        ["10"],
      );
      assert.equal(filter.best.value, "acme/obsidian#731");
      assert.equal(filter.count.value, 1);
      const row = filter.groups.value[0]?.prs[0];
      if (query === "ShownLabel") {
        assert.deepEqual(row?.highlights?.label, [[0, 9]]);
      }
      if (query === "AMBER ZEPELIN") {
        assert.ok(row?.highlights?.title.length);
      }
    } finally {
      scope.stop();
    }
  });
}

function updatedGroups(): GroupSummary[] {
  const primary = groups[0];
  assert.ok(primary);
  const first = primary.prs[0];
  assert.ok(first);

  return [
    {
      ...primary,
      count: 2,
      prs: [
        first,
        { tabId: 2, key: "acme/obsidian#2", label: "obsidian#2", title: "Amber", head: null },
      ],
    },
  ];
}

test("empty search preserves source view; ranked search rejects distant matches and follows updates", async () => {
  const scope = effectScope();
  const source = ref(groups);
  const query = ref("");
  try {
    const filter = scope.run(() => useFuzzyFilter(source, query));
    assert.ok(filter);
    assert.equal(filter.groups.value, source.value);
    assert.equal(filter.count.value, 2);
    query.value = "zzqqxxxx";
    await nextTick();
    assert.equal(filter.groups.value.length, 0);
    assert.equal(filter.best.value, null);
    source.value = updatedGroups();
    query.value = "Amber";
    await nextTick();
    assert.equal(filter.groups.value[0]?.prs[0]?.key, "acme/obsidian#2");
    assert.equal(filter.count.value, 2);
    query.value = " ";
    await nextTick();
    assert.equal(filter.groups.value, source.value);
  } finally {
    scope.stop();
  }
});
