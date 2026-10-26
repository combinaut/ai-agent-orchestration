import assert from "node:assert/strict";
import { test } from "vitest";
import type {
  Entry,
  GroupLike,
  Meta,
  Pr,
  PrKey,
  PrRow,
  Summary,
  TabId,
  TabLike,
  WindowId,
} from "#/types";
import {
  headKey,
  lookupHeads,
  MAX_PANE_LOOKUPS,
  effectiveRepos,
  pickedRepo,
  showRepoPicker,
  duplicateTabIds,
  findOpenTab,
  isPrUrl,
  prKey,
  tabUpdateMatters,
  parsePrUrl,
  parseTarget,
  summarize,
} from "#/core";
import { duplicateText, summaryText } from "#/texts";
import { rules } from "./helpers";

test("a bare number means acme/webapp", () => {
  assert.equal(parseTarget("7", { org: "", defaultRepo: "" }), null);
  assert.equal(parseTarget("7", { org: "acme", defaultRepo: "" }), null);

  for (const input of ["2972", " 2972 ", "#2972"]) {
    const pr = parseTarget(input, rules);
    assert.equal(pr?.key, "acme/webapp#2972");
    assert.equal(pr?.url, "https://github.com/acme/webapp/pull/2972");
  }
});

test("a pasted URL decides the repo", () => {
  for (const input of [
    "https://github.com/acme/ai-agent-orchestration/pull/18",
    "https://github.com/acme/ai-agent-orchestration/pull/18/files#diff-1",
    "github.com/acme/ai-agent-orchestration/pull/18?w=1",
  ]) {
    assert.equal(parseTarget(input, rules)?.key, "acme/ai-agent-orchestration#18");
  }
});

test("anything else is not a target", () => {
  for (const input of [
    "",
    "0",
    "abc",
    "12a",
    "https://github.com/other/repo/pull/1",
    "https://github.com/acme/webapp/issues/3",
    "https://github.com/acme/webapp/pull/",
  ]) {
    assert.equal(parseTarget(input, rules), null, input);
  }
});

test("only acme PR URLs are PR tabs", () => {
  assert.equal(
    parsePrUrl("https://github.com/acme/webapp/pull/7/commits", "acme")?.key,
    "acme/webapp#7",
  );
  assert.equal(parsePrUrl("https://github.com/acme/webapp/pulls", "acme"), null);
  assert.equal(parsePrUrl("https://github.com/acme/webapp/pull/77x", "acme"), null);
});

test("PR URLs past the native host's limits are not PR tabs", () => {
  assert.equal(parsePrUrl(`https://github.com/acme/${"r".repeat(101)}/pull/1`, "acme"), null);
  assert.equal(
    parsePrUrl(`https://github.com/acme/${"r".repeat(100)}/pull/1`, "acme")?.repo.length,
    100,
  );
  assert.equal(parsePrUrl("https://github.com/acme/webapp/pull/1234567890", "acme"), null);
  assert.equal(parsePrUrl("https://github.com/acme/webapp/pull/0", "acme"), null);
});

test("PR identities ignore owner and repo casing", () => {
  const pr = parsePrUrl("https://github.com/Acme/Webapp/pull/1", "acme");
  assert.equal(pr?.key, parseTarget("1", rules)?.key);
  assert.equal(pr?.url, "https://github.com/acme/webapp/pull/1");
});

function entry(id: TabId, windowId: WindowId, key: PrKey): Entry {
  return { tab: { id, windowId }, pr: { key } };
}

test("an open tab for the PR is found, preferring the focused window", () => {
  const entries = [
    entry(1, 10, "acme/webapp#1"),
    entry(2, 20, "acme/webapp#2"),
    entry(3, 30, "acme/webapp#2"),
  ];

  assert.equal(findOpenTab(entries, "acme/webapp#2", 30)?.tab.id, 3);
  assert.equal(findOpenTab(entries, "acme/webapp#2", 10)?.tab.id, 2);
  assert.equal(findOpenTab(entries, "acme/webapp#1", undefined)?.tab.id, 1);
});

test("a PR without a tab is not open", () => {
  assert.equal(findOpenTab([entry(1, 10, "acme/webapp#1")], "acme/webapp#9", 10), null);
});

function prEntry(id: TabId, groupId: number, number: number): Entry<TabLike, Pr> {
  const pr = parsePrUrl(`https://github.com/acme/webapp/pull/${number}`, "acme");

  assert.ok(pr);

  return { tab: { id, groupId }, pr };
}

const SUMMARIZED: Summary = {
  prs: 4,
  groups: [
    {
      id: "7",
      title: "Search",
      color: "blue",
      count: 2,
      prs: [
        { key: "acme/webapp#1", label: "webapp#1", title: "Fix", head: null },
        {
          key: "acme/webapp#2",
          label: "webapp#2",
          title: "Fork",
          head: { repo: "Me/WebApp", branch: "f" },
        },
      ],
    },
    {
      id: "8",
      title: "Infra",
      color: "red",
      count: 1,
      prs: [
        {
          key: "acme/webapp#3",
          label: "webapp#3",
          title: "",
          head: { repo: "acme/webapp", branch: "infra" },
        },
      ],
    },
  ],
};

test("the summary counts distinct PRs and lists the PRs of each group with their head", () => {
  const entries = [
    prEntry(1, 7, 1),
    prEntry(2, 7, 1),
    prEntry(3, 7, 2),
    prEntry(4, 8, 3),
    prEntry(5, -1, 4),
  ];
  const groups = new Map<number | undefined, GroupLike>([
    [7, { title: "Search", color: "blue" }],
    [8, { title: "Infra", color: "red" }],
  ]);
  const meta: Meta = {
    "acme/webapp#1": { title: "Fix", headRefName: "fix", headRepository: null },
    "acme/webapp#2": {
      title: "Fork",
      headRefName: "f",
      headRepository: { nameWithOwner: "Me/WebApp" },
    },
    "acme/webapp#3": { headRefName: "infra" },
  };

  const result = summarize(entries, groups, meta);

  assert.deepEqual(result, SUMMARIZED);
  assert.equal(summaryText(result), "4 PRs in 2 groups");
  assert.equal(
    summaryText({ prs: 1, groups: [{ id: "7", title: "Search", count: 1, prs: [] }] }),
    "1 PR in 1 group",
  );
  assert.equal(summaryText({ prs: 0, groups: [] }), "No PR tabs open");
});

function row(n: number, repo: string, branch: string): PrRow {
  return { key: `acme/webapp#${n}`, label: `webapp#${n}`, title: "", head: { repo, branch } };
}

test("pane lookups ask for each distinct head once, in list order, at most the cap", () => {
  const many = Array.from({ length: MAX_PANE_LOOKUPS + 5 }, (_, i) =>
    row(i + 10, "acme/webapp", `b${i}`),
  );
  const groups = [
    {
      id: "1",
      title: "a",
      count: 3,
      prs: [
        row(1, "acme/webapp", "x"),
        row(2, "ACME/WebApp", "x"),
        { ...row(3, "", ""), head: null },
      ],
    },
    { id: "2", title: "b", count: many.length, prs: many },
  ];

  const heads = lookupHeads(groups);

  assert.equal(heads.length, MAX_PANE_LOOKUPS);
  assert.deepEqual(heads.slice(0, 2), [
    { repo: "acme/webapp", branch: "x" },
    { repo: "acme/webapp", branch: "b0" },
  ]);
  assert.equal(headKey({ repo: "Acme/WebApp", branch: "Feat/X" }), "acme/webapp:Feat/X");
});

test("only PR pages count as PR tab updates", () => {
  assert.equal(isPrUrl("https://github.com/acme/webapp/pull/1/files"), true);

  for (const url of [
    "https://example.com/",
    "https://github.com/acme/webapp",
    "https://github.com/acme/webapp/pulls",
    undefined,
  ]) {
    assert.equal(isPrUrl(url), false, String(url));
  }
});

test("an unrelated tab's load or title change skips the grouping and alarm path", () => {
  const other = { url: "https://example.com/" };

  assert.equal(tabUpdateMatters({ status: "complete" }, other, false), false);
  assert.equal(tabUpdateMatters({ title: "News" }, other, false), false);
  assert.equal(tabUpdateMatters({ status: "complete" }, {}, false), false);
});

test("PR tabs, tabs known as PR tabs and URL changes take the grouping and alarm path", () => {
  assert.equal(
    tabUpdateMatters(
      { status: "complete" },
      { url: "https://github.com/acme/webapp/pull/1" },
      false,
    ),
    true,
  );
  assert.equal(tabUpdateMatters({ title: "x" }, {}, true), true);
  assert.equal(
    tabUpdateMatters({ url: "https://example.com/" }, { url: "https://example.com/" }, false),
    true,
  );
  assert.equal(
    tabUpdateMatters({ groupId: 5 }, { url: "https://github.com/acme/webapp/pull/1" }, true),
    false,
  );
});

test("prKey is one key per PR: owner and repo lowercased, sub-page, query and fragment ignored, any owner", () => {
  const key = "acme/webapp#7";

  for (const url of [
    "https://github.com/acme/webapp/pull/7",
    "https://github.com/Acme/Webapp/pull/7/files",
    "https://www.github.com/acme/webapp/pull/7/commits?w=1#diff-1",
    "https://github.com/acme/webapp/pull/7#issuecomment-1",
  ]) {
    assert.equal(prKey(url), key, url);
  }

  assert.equal(prKey("https://github.com/other/repo/pull/12"), "other/repo#12");

  for (const url of [
    "",
    null,
    "https://github.com/acme/webapp/pull/0",
    "https://github.com/acme/webapp/pull/7x",
    "https://github.com/acme/webapp/issues/7",
    "https://gitlab.com/acme/webapp/pull/7",
  ]) {
    assert.equal(prKey(url), null, String(url));
  }
});

const prUrl = (n: number) => `https://github.com/acme/webapp/pull/${n}`;

test("duplicateTabIds keeps the active tab, else the lowest ID, per PR", () => {
  assert.deepEqual(
    duplicateTabIds([
      { id: 8, url: prUrl(1) },
      { id: 2, url: prUrl(1) },
      { id: 5, url: prUrl(1) },
    ]),
    [8, 5],
  );
  assert.deepEqual(
    duplicateTabIds([
      { id: 2, url: prUrl(1) },
      { id: 8, url: prUrl(1), active: true },
    ]),
    [2],
  );
  assert.deepEqual(
    duplicateTabIds([
      { id: 1, url: prUrl(1) },
      { id: 2, url: prUrl(2) },
    ]),
    [],
  );
});

test("duplicateText says how many tabs closed", () => {
  assert.equal(duplicateText(0), "No duplicate PR tabs.");
  assert.equal(duplicateText(1), "Closed 1 duplicate PR tab.");
  assert.equal(duplicateText(3), "Closed 3 duplicate PR tabs.");
});

test("duplicateTabIds keeps the active tab of the focused window over another window's", () => {
  const url = "https://github.com/acme/webapp/pull/1";
  const tabs = [
    { id: 1, url, windowId: 1, active: true },
    { id: 2, url, windowId: 2, active: true },
  ];

  assert.deepEqual(duplicateTabIds(tabs, 2), [1]);
  assert.deepEqual(duplicateTabIds(tabs, 1), [2]);
});

const multi = { ...rules, repos: ["api", "Webapp", "docs"] };

test("defaultRepo is first in the effective repos, without repeats", () => {
  assert.deepEqual(effectiveRepos(multi), ["webapp", "api", "docs"]);
  assert.deepEqual(effectiveRepos({ ...rules, defaultRepo: "", repos: ["api"] }), ["api"]);
  assert.deepEqual(effectiveRepos(rules), ["webapp"]);
});

test("the dropdown shows only with two or more repos", () => {
  assert.equal(showRepoPicker(rules), false);
  assert.equal(showRepoPicker({ ...rules, repos: ["Webapp"] }), false);
  assert.equal(showRepoPicker(multi), true);
});

test("the selected repo is the last pick while listed, else defaultRepo", () => {
  assert.equal(pickedRepo(multi, "docs"), "docs");
  assert.equal(pickedRepo(multi, "gone"), "webapp");
  assert.equal(pickedRepo(multi, ""), "webapp");
});

test("a bare number goes to the picked repo", () => {
  assert.equal(parseTarget("5", multi, "api")?.key, "acme/api#5");
  assert.equal(parseTarget("#5", multi, "docs")?.url, "https://github.com/acme/docs/pull/5");
  assert.equal(parseTarget("5", multi)?.key, "acme/webapp#5");
  assert.equal(parseTarget("5", multi, "unlisted")?.key, "acme/webapp#5");
});

test("<repo>#<number> is accepted for listed repos only", () => {
  assert.equal(parseTarget("api#12", multi, "docs")?.key, "acme/api#12");
  assert.equal(parseTarget(" DOCS#3 ", multi)?.key, "acme/docs#3");
  assert.equal(parseTarget("webapp#9", rules)?.key, "acme/webapp#9");

  for (const input of ["other#1", "api#0", "api#", "#api", "api#1x", "api #1"]) {
    assert.equal(parseTarget(input, multi), null, input);
  }

  assert.equal(parseTarget("api#1", rules), null);
  assert.equal(parseTarget("api#1", { ...multi, org: "" }), null);
});
