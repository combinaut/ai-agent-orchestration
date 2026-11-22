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
      row("web-app", 2968, "Fix Deploy preview"),
      row("web-app", 2941, "deploy banner"),
    ],
  },
  {
    id: "2",
    title: "deploy-tools",
    count: 2,
    prs: [row("deploy-tools", 47, "Upload"), row("deploy-tools", 52, "Retry")],
  },
  { id: "3", title: "docs", count: 1, prs: [row("docs", 301, "Shortcuts")] },
];

function filter(query: SearchQuery) {
  return useSearchFilter(ref(groups), ref(query));
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
  const result = filter({ text: "  ", numberOnly: false });

  assert.equal(result.active.value, false);
  assert.deepEqual(result.groups.value, groups);
  assert.equal(result.count.value, 6);
});

test("search keeps matching rows, all rows of a matching repo, and reports matches/total", () => {
  const result = filter({ text: " deploy ", numberOnly: false });
  const [web, tools] = result.groups.value;

  assert.equal(result.active.value, true);
  assert.equal(result.count.value, 4);
  assert.deepEqual(
    result.groups.value.map((group) => group.title),
    ["web-app", "deploy-tools"],
  );
  assert.deepEqual(
    web?.prs.map((pr) => pr.tabId),
    [2968, 2941],
  );
  assert.equal(web?.total, 3);
  assert.deepEqual(web?.prs[0]?.highlights, { number: [], title: [[4, 9]] });
  assert.deepEqual(tools?.titleMatches, [[0, 5]]);
  assert.equal(tools?.count, 2);
  assert.equal(tools?.total, 2);
});

test("a number query matches the PR number, with or without #", () => {
  assert.deepEqual(
    filter({ text: "#301", numberOnly: false }).groups.value.map((group) => group.title),
    ["docs"],
  );
  const result = filter({ text: "2972", numberOnly: true });

  assert.equal(result.count.value, 1);
  assert.deepEqual(result.groups.value[0]?.prs[0]?.highlights, { number: [[1, 4]], title: [] });
});

test("number-only queries ignore titles and repo names", () => {
  assert.equal(filter({ text: "deploy", numberOnly: true }).count.value, 0);
});
