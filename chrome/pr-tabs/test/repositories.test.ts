import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import type { FakeTabs, FakeTabGroups } from "#test/types";
import { parsePrUrl, parseTarget, stillShowsPr } from "#/core";
import { recentRepositories, validRepository } from "#/repositories";
import { CONFIGURED, fakeChrome, installFakeChrome, world } from "#test/fakeChrome";

installFakeChrome();
afterEach(() => {
  vi.restoreAllMocks();
});

test("repo order uses recency across owner formats, then never-used names A–Z", () => {
  assert.deepEqual(
    recentRepositories(
      ["zoo", "api", "other/app", "docs", "alpha"],
      {
        "acme/zoo": 10,
        "other/app": 20,
      },
      "acme",
    ),
    ["other/app", "zoo", "alpha", "api", "docs"],
  );
  assert.equal(validRepository("Acme/web-app"), true);
  for (const value of ["api", "a/b/c", "-a/b", "a-/b", "a/..", "a/a b", "a/", "/b"]) {
    assert.equal(validRepository(value), false, value);
  }
});

test("explicit repositories parse, scan and close under their own owner", () => {
  const rules = { org: "acme", defaultRepo: "webapp", openAll: "", repos: ["other/api"] };
  const url = "https://github.com/other/api/pull/42";
  assert.equal(parseTarget("42", rules, "other/api")?.url, url);
  assert.equal(parseTarget("other/api#42", rules)?.url, url);
  assert.equal(parseTarget(url, rules)?.url, url);
  assert.equal(parsePrUrl(url, rules)?.key, "other/api#42");
  assert.equal(stillShowsPr({ url }, "other/api#42", rules), true);
  assert.equal(parsePrUrl("https://github.com/other/private/pull/42", rules), null);
});

function repositoryHost() {
  world.store = {};
  world.tabs = [];
  world.created = [];
  world.hostSettings = CONFIGURED;
  const sent: unknown[] = [];
  world.nativeHandler = async (message) => {
    sent.push(message);

    return { ok: true, newest: {} };
  };

  return sent;
}

test("concurrent Settings edits persist, reject duplicates and flow to number lookup and tabs", async () => {
  const sent = repositoryHost();
  const worker = await import("#/background");
  await Promise.all([worker.editRepository(" Other/API "), worker.editRepository("acme/docs")]);
  assert.deepEqual(await worker.repositorySettings(), {
    configured: ["acme/webapp"],
    added: ["other/api", "acme/docs"],
  });
  await assert.rejects(worker.editRepository("ACME/WEBAPP"), /already listed/);
  await assert.rejects(worker.editRepository("other/api"), /already listed/);
  await assert.rejects(worker.editRepository("bad"), /valid owner\/name/);
  await worker.editRepository("acme/webapp", true);
  assert.deepEqual((await worker.repositorySettings()).configured, ["acme/webapp"]);
  await worker.repoChoices();
  assert.deepEqual(sent.at(-1), {
    type: "repoNumbers",
    org: "acme",
    repos: ["webapp", "other/api", "acme/docs"],
  });
  await worker.goTo("42", "other/api");
  assert.equal(world.created.at(-1)?.url, "https://github.com/other/api/pull/42");
  assert.ok(world.store.prTabsRepoUsage?.["other/api"]);
  assert.equal((await worker.prEntries({ org: "acme", repos: ["other/api"] })).length, 1);
  await worker.editRepository("other/api", true);
  assert.deepEqual(world.store.prTabsAddedRepos, ["acme/docs"]);
  assert.equal((await worker.repoChoices()).repos.includes("other/api"), false);
});

test("repo picks serialize usage updates and persist recent-first order", async () => {
  let now = 10;
  vi.spyOn(Date, "now").mockImplementation(() => now++);
  world.store = { prTabsAddedRepos: ["acme/docs", "acme/api"] };
  const worker = await import("#/background");
  await Promise.all([worker.pickRepo("acme/docs"), worker.pickRepo("acme/api")]);
  assert.ok(world.store.prTabsRepoUsage?.["acme/docs"]);
  assert.ok(world.store.prTabsRepoUsage?.["acme/api"]);
  assert.deepEqual((await worker.repoChoices()).repos, ["acme/api", "acme/docs", "webapp"]);
});

async function groupTabs(
  groups: chrome.tabGroups.TabGroup[],
  options: chrome.tabs.GroupOptions,
): Promise<number> {
  const id = options.groupId ?? groups.length + 1;
  if (options.groupId === undefined) {
    groups.push({ id, windowId: 1, title: "", color: "grey", collapsed: false, shared: false });
  }
  for (const tab of world.tabs) {
    if ([options.tabIds].flat().includes(tab.id)) {
      tab.groupId = id;
    }
  }

  return id;
}

function groupingHost(): chrome.tabGroups.TabGroup[] {
  const groups: chrome.tabGroups.TabGroup[] = [];
  world.store = {};
  world.tabs = [];
  vi.spyOn(fakeChrome.tabGroups as FakeTabGroups, "query").mockImplementation(async (filter) =>
    groups.filter(
      (group) =>
        (filter.windowId === undefined || group.windowId === filter.windowId) &&
        (filter.title === undefined || group.title === filter.title),
    ),
  );
  vi.spyOn(fakeChrome.tabGroups, "update").mockImplementation(async (id, values) => {
    Object.assign(groups.find((group) => group.id === id) ?? {}, values);
  });
  vi.spyOn(fakeChrome.tabs as FakeTabs, "group").mockImplementation((options) =>
    groupTabs(groups, options),
  );

  return groups;
}

test("concurrent grouping keeps persisted allocations across worker restarts and frees closed names", async () => {
  const groups = groupingHost();
  const worker = await import("#/background");
  await Promise.all(
    ["group-2", "group-9", "group-10"].map((repo) =>
      worker.goTo(`https://github.com/acme/${repo}/pull/1`),
    ),
  );
  assert.equal(new Set(groups.map((item) => item.color)).size, 3);
  const allocated = { ...world.store.prTabsGroupColors };
  const tag = "colour-restart";
  const restarted = await import(/* @vite-ignore */ `#/background.ts?${tag}`);
  await restarted.goTo("https://github.com/acme/group-2/pull/2");
  assert.deepEqual(world.store.prTabsGroupColors, allocated);
  const freedColor = groups[1]?.color;
  groups.splice(1, 1);
  await restarted.goTo("https://github.com/acme/new/pull/1");
  assert.equal(world.store.prTabsGroupColors?.new, freedColor);
  assert.equal(Object.hasOwn(world.store.prTabsGroupColors ?? {}, "group-9"), false);
});
