import assert from "node:assert/strict";
import { ref } from "vue";
import { test } from "vitest";
import { matchRanges, prNumberText, useSearchFilter } from "#/composables/useSearchFilter";
import type { GroupSummary, PrRow, SearchQuery } from "#/types";

function row(repo: string, number: number, title: string): PrRow {
  return {
    tabId: number,
    key: `acme/${repo}#${number}`,
    label: `${repo}#${number}`,
    title,
    head: null,
  };
}

const groups: GroupSummary[] = [
  {
    id: "1",
    title: "web-app",
    count: 3,
    prs: [
      row("web-app", 2972, "Add saved carts"),
      row("web-app", 2968, "Fix Amber Zeppelin preview"),
      {
        ...row("web-app", 2941, "Quarterly banner"),
        head: { repo: "acme/web-app", branch: "velvetorbit" },
      },
    ],
  },
  {
    id: "2",
    title: "CobaltArchitect",
    count: 2,
    prs: [row("tools", 47, "Upload"), row("tools", 52, "Retry")],
  },
  { id: "3", title: "docs", count: 1, prs: [row("docs", 301, "Shortcuts")] },
];

function filter(query: SearchQuery) {
  return useSearchFilter(ref(groups), ref(query));
}

function search(text: string) {
  return filter({ text, numberOnly: false });
}

test("matchRanges finds every case-insensitive occurrence", () => {
  assert.deepEqual(matchRanges("Deploy and deploy", "DEPLOY"), [
    [0, 5],
    [11, 16],
  ]);
  assert.deepEqual(matchRanges("abc", ""), []);
  assert.equal(prNumberText(row("x", 515, "")), "#515");
});

test("an empty query keeps every group untouched", () => {
  const result = search("  ");

  assert.equal(result.active.value, false);
  assert.deepEqual(result.groups.value, groups);
  assert.equal(result.count.value, 6);
});

test("a typo still finds the row, marked and counted as matches/total", () => {
  const result = search("AMBER ZEPELIN");
  const [web] = result.groups.value;

  assert.equal(result.count.value, 1);
  assert.deepEqual(
    result.groups.value.map((group) => group.title),
    ["web-app"],
  );
  assert.equal(web?.prs[0]?.tabId, 2968);
  assert.equal(web?.total, 3);
  assert.ok((web?.prs[0]?.highlights?.title.length ?? 0) > 0);
});

for (const [query, tabId] of [
  ["velvetorbit", 2941],
  ["#301", 301],
  ["Shortcuts", 301],
] as const) {
  test(`Fuse indexes ${query}`, () => {
    const result = search(query);

    assert.ok(
      result.groups.value.some((group) => group.prs.some((pr) => pr.tabId === tabId)),
      query,
    );
  });
}

test("a matching group name shows all its rows and marks the name", () => {
  const result = search("CobaltArchitect");
  const cobalt = result.groups.value.find((group) => group.id === "2");

  assert.equal(cobalt?.count, 2);
  assert.equal(cobalt?.total, 2);
  assert.ok((cobalt?.titleMatches?.length ?? 0) > 0);
});

test("a number match is marked on the shown #number", () => {
  const docs = search("301").groups.value.find((group) => group.id === "3");

  assert.deepEqual(docs?.prs[0]?.highlights?.number, [[1, 3]]);
});

test("number-only queries match PR numbers only, as a substring", () => {
  const result = filter({ text: "2972", numberOnly: true });

  assert.equal(result.count.value, 1);
  assert.deepEqual(result.groups.value[0]?.prs[0]?.highlights, { number: [[1, 4]], title: [] });
  assert.equal(filter({ text: "amber", numberOnly: true }).count.value, 0);
});

test("a query without matches leaves no groups and counts zero", () => {
  const result = search("zzzzqqqq");

  assert.deepEqual([result.groups.value, result.count.value], [[], 0]);
});

test("the filter follows changes to the query and the groups", () => {
  const source = ref(groups);
  const query = ref<SearchQuery>({ text: "Shortcuts", numberOnly: false });
  const result = useSearchFilter(source, query);

  assert.equal(result.count.value, 1);
  query.value = { text: "Upload", numberOnly: false };
  assert.equal(result.groups.value[0]?.prs[0]?.tabId, 47);
  source.value = groups.slice(0, 1);
  assert.equal(result.count.value, 0);
});
