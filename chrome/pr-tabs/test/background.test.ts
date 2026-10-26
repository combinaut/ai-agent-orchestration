import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import type * as Background from "#/background";
import { MAX_PANE_LOOKUPS } from "#/core";
import type { HostRequest, Meta } from "#/types";
import type { FakeTab } from "#test/types";
import { CONFIGURED, fakeChrome, installFakeChrome, world } from "./fakeChrome";

installFakeChrome();

// A fresh service worker instance: its own settings cache, ownership and timers.
function freshWorker(tag: string): Promise<typeof Background> {
  return import(/* @vite-ignore */ `#/background.ts?${tag}`);
}

const {
  closeDuplicates,
  focusPane,
  goTo,
  openAll,
  panes,
  prEntries,
  checkNow: chromeMessageCheck,
} = await import("#/background");

const realNow = Date.now;

afterEach(() => {
  vi.restoreAllMocks();
  Date.now = realNow;
  world.hostSettings = CONFIGURED;
});

function fakeClock() {
  let now = realNow();

  Date.now = () => now;

  return {
    advance(ms: number) {
      now += ms;
    },
  };
}

function tabWithId(id: number): FakeTab {
  const tab = world.tabs.find((candidate) => candidate.id === id);
  assert.ok(tab);

  return tab;
}

test("goTo names the setting that is missing", async () => {
  world.hostSettings = { ...CONFIGURED, org: "" };

  const noOrg = await freshWorker("missing-org");

  assert.deepEqual(await noOrg.goTo("7"), {
    error: "Set pr_tabs.org in orchestration.toml",
  });

  world.hostSettings = { ...CONFIGURED, defaultRepo: "" };

  const noRepo = await freshWorker("missing-repo");

  assert.deepEqual(await noRepo.goTo("7"), {
    error: "Set pr_tabs.default_repo in orchestration.toml",
  });
});

async function repeat(times: number, task: () => Promise<unknown>): Promise<void> {
  for (let i = 0; i < times; i++) {
    await task();
  }
}

async function rejectsGoTo(worker: typeof Background, input: string): Promise<void> {
  await assert.rejects(worker.goTo(input), /host unavailable/);
}

const OFFLINE = { ok: false, error: "host unavailable" };

test("cached settings serve an offline host, with backed-off retries", async () => {
  const clock = fakeClock();

  world.tabs = [];
  world.store = {};
  world.grouped = [];
  world.hostSettings = { ...CONFIGURED, defaultRepo: "cached" };
  await (await freshWorker("cache-seed")).goTo("1");

  assert.deepEqual(world.store.prTabsSettings, world.hostSettings);

  const good = world.store.prTabsSettings;

  world.hostSettings = OFFLINE;

  const offline = await freshWorker("cache-offline");
  const before = world.settingsCalls;

  await offline.goTo("2");

  assert.equal(world.grouped.at(-1)?.title, "cached");

  await repeat(5, () => offline.goTo("2"));

  assert.equal(world.settingsCalls, before + 1, "failed host retries are backed off");
  assert.deepEqual(world.store.prTabsSettings, good);

  clock.advance(60001);
  world.hostSettings = { ...CONFIGURED, defaultRepo: "docs" };
  await offline.goTo("3");

  assert.equal(world.settingsCalls, before + 2);
  assert.equal(world.store.prTabsSettings?.defaultRepo, "docs");
  assert.ok(world.tabs.some((tab) => tab.url === "https://github.com/acme/docs/pull/3"));
});

test("an offline host without cached settings fails and backs off retries", async () => {
  const clock = fakeClock();

  world.tabs = [];
  world.store = {};
  world.hostSettings = OFFLINE;

  const empty = await freshWorker("cache-empty");
  const initial = world.settingsCalls;

  await repeat(5, () => rejectsGoTo(empty, "4"));

  assert.equal(world.settingsCalls, initial + 1, "even a missing cache backs off retries");

  clock.advance(60001);
  world.hostSettings = CONFIGURED;
  await empty.goTo("4");

  assert.equal(world.settingsCalls, initial + 2);

  world.store = {};
  world.tabs = [];
});

test("PR tabs whose URL differs from the org in case are kept; other orgs are dropped", async () => {
  world.tabs = [
    { id: 1, url: "https://github.com/Acme/webapp/pull/1" },
    { id: 2, url: "https://github.com/acme/webapp/pull/2" },
    { id: 3, url: "https://github.com/other/webapp/pull/3" },
  ];

  const entries = await prEntries({ org: "acme" });

  assert.deepEqual(
    entries.map((entry) => entry.tab.id),
    [1, 2],
  );
});

test("a no-cache settings failure arms a retry that recovers after backoff", async () => {
  const clock = fakeClock();

  world.tabs = [];
  world.store = {};
  world.createdAlarms = [];
  world.settingsCalls = 0;
  world.hostSettings = OFFLINE;
  await freshWorker("settings-retry-alarm");
  await world.installedListener();

  assert.equal(world.createdAlarms.at(-1)?.options.delayInMinutes, 1);

  clock.advance(60001);
  world.hostSettings = { ...CONFIGURED };
  world.alarmListener({ name: "pr-tabs-poll" });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(world.settingsCalls, 2);

  world.store = {};
  world.tabs = [];
});

test("a PR opened by goTo while Open all searches is not opened a second time", async () => {
  world.tabs = [];
  world.store = {};

  const url = "https://github.com/acme/webapp/pull/7";

  world.nativeHandler = async () => {
    await goTo("7");

    return { ok: true, total: 1, prs: [{ url, title: "t", headRefName: "b" }], hasNextPage: false };
  };

  await openAll();

  assert.equal(world.tabs.filter((tab) => tab.url === url).length, 1);
});

const PR = "https://github.com/acme/webapp/pull";
const ids = () => world.tabs.map((tab) => tab.id);

test("goTo focuses an open tab of the same PR on another sub-page and opens no second one", async () => {
  world.tabs = [{ id: 1, url: `${PR}/7/files?w=1#diff`, windowId: 1, groupId: -1 }];

  assert.deepEqual(await goTo("https://GitHub.com/Acme/Webapp/pull/7"), {
    focused: "acme/webapp#7",
  });
  assert.deepEqual(ids(), [1]);
  assert.equal(world.tabs[0]?.active, true);
});

test("Open all skips a PR that is open on a sub-page", async () => {
  world.tabs = [{ id: 1, url: `${PR}/7/commits`, windowId: 1, groupId: -1 }];
  world.store = {};
  world.nativeHandler = async () => ({
    ok: true,
    total: 1,
    prs: [{ url: `${PR}/7`, title: "t", headRefName: "b" }],
    hasNextPage: false,
  });

  assert.deepEqual(await openAll(), { opened: 0, remaining: 0, estimated: false });
  assert.deepEqual(ids(), [1]);
});

test("Open all opens PRs in background tabs and goTo opens one in front", async () => {
  world.tabs = [];
  world.store = {};
  world.created = [];
  world.nativeHandler = async () => ({
    ok: true,
    total: 2,
    prs: [`${PR}/7`, `${PR}/8`].map((url) => ({ url, title: "t", headRefName: "b" })),
    hasNextPage: false,
  });

  await openAll();
  await goTo("9");

  assert.deepEqual(
    world.created.map((created) => [created.url, created.active]),
    [
      [`${PR}/7`, false],
      [`${PR}/8`, false],
      [`${PR}/9`, true],
    ],
  );
});

test("Close duplicate tabs closes the extras of a PR across windows and reports the count", async () => {
  world.tabs = [
    { id: 5, url: `${PR}/7`, windowId: 2 },
    { id: 3, url: `${PR}/7/files`, windowId: 1 },
    { id: 4, url: `${PR}/7/checks#x`, windowId: 1 },
  ];

  assert.deepEqual(await closeDuplicates(), { closed: 2 });
  assert.deepEqual(ids(), [3]);
});

test("Close duplicate tabs keeps the active tab, even a newer one", async () => {
  world.tabs = [
    { id: 3, url: `${PR}/7`, windowId: 1 },
    { id: 9, url: `${PR}/7/files`, windowId: 1, active: true },
  ];

  await closeDuplicates();

  assert.deepEqual(ids(), [9]);
});

test("Close duplicate tabs leaves other PRs, non-PR tabs and incognito tabs alone", async () => {
  world.tabs = [
    { id: 1, url: `${PR}/7` },
    { id: 2, url: `${PR}/8` },
    { id: 3, url: "https://github.com/acme/webapp/issues/7" },
    { id: 4, url: "https://example.com/" },
    { id: 5, url: `${PR}/7`, incognito: true },
    { id: 6, url: "https://github.com/other/repo/pull/7" },
    { id: 7, url: "https://github.com/Other/Repo/pull/7/files" },
  ];

  assert.deepEqual(await closeDuplicates(), { closed: 1 });
  assert.deepEqual(ids(), [1, 2, 3, 4, 5, 6]);
});

test("goTo does not reuse an incognito tab", async () => {
  world.tabs = [{ id: 1, url: `${PR}/7`, windowId: 1, groupId: -1, incognito: true }];
  assert.deepEqual(await goTo("7"), { opened: "acme/webapp#7" });
});

test("Close duplicate tabs ignores tabs outside normal windows", async () => {
  world.tabs = [
    { id: 1, url: `${PR}/7` },
    { id: 2, url: `${PR}/7`, windowType: "popup" },
    { id: 3, url: `${PR}/7`, windowType: "app" },
  ];

  assert.deepEqual(await closeDuplicates(), { closed: 0 });
  assert.deepEqual(ids(), [1, 2, 3]);
});

// Runs change() when closeDuplicates re-reads the tabs for the first time after the initial scan.
function changeAfterScan(change: () => void) {
  const query = fakeChrome.tabs.query;
  let calls = 0;

  fakeChrome.tabs.query = async (tabQuery) => {
    if (++calls === 2) {
      change();
    }

    return query(tabQuery);
  };

  return () => {
    fakeChrome.tabs.query = query;
  };
}

test("Close duplicate tabs does not close a tab that is no longer a duplicate", async () => {
  world.tabs = [
    { id: 1, url: `${PR}/7` },
    { id: 2, url: `${PR}/7` },
  ];

  const restore = changeAfterScan(() => {
    tabWithId(1).url = `${PR}/8`;
  });

  try {
    assert.deepEqual(await closeDuplicates(), { closed: 0 });
  } finally {
    restore();
  }

  assert.deepEqual(ids(), [1, 2]);
});

test("Close duplicate tabs does not close a tab that became active meanwhile", async () => {
  world.tabs = [
    { id: 1, url: `${PR}/7` },
    { id: 2, url: `${PR}/7` },
  ];

  const restore = changeAfterScan(() => {
    tabWithId(2).active = true;
  });

  try {
    assert.deepEqual(await closeDuplicates(), { closed: 0 });
  } finally {
    restore();
  }

  assert.deepEqual(ids(), [1, 2]);
});

test("Close duplicate tabs does not close the last tab when the kept one is gone", async () => {
  world.tabs = [
    { id: 1, url: `${PR}/7` },
    { id: 2, url: `${PR}/7` },
    { id: 3, url: `${PR}/7` },
  ];

  const restore = changeAfterScan(() => {
    world.tabs = world.tabs.filter((tab) => tab.id !== 1);
  });

  try {
    await closeDuplicates();
  } finally {
    restore();
  }

  assert.deepEqual(ids(), [2]);
});

test("Close duplicate tabs does not close a tab that moved to another duplicated PR", async () => {
  world.tabs = [
    { id: 5, url: `${PR}/7` },
    { id: 6, url: `${PR}/7` },
    { id: 3, url: `${PR}/8` },
  ];

  const restore = changeAfterScan(() => {
    tabWithId(6).url = `${PR}/8`;
  });

  try {
    await closeDuplicates();
  } finally {
    restore();
  }

  assert.deepEqual(ids(), [5, 6, 3]);
});

test("Close duplicate tabs keeps the active tab of the focused window (window 1)", async () => {
  world.tabs = [
    { id: 1, url: `${PR}/7`, windowId: 2, active: true },
    { id: 2, url: `${PR}/7`, windowId: 1, active: true },
  ];

  await closeDuplicates();

  assert.deepEqual(ids(), [2]);
});

test("existing check cadence regroups tabs after ownership changes or disappears", async () => {
  world.tabs = [{ id: 1, url: `${PR}/7`, windowId: 1, groupId: -1 }];
  world.store = {};
  world.grouped = [];

  let owners: Record<string, string> = { "acme/webapp:feature": "Finder" };

  world.nativeHandler = async () => ({
    ok: true,
    owners,
    prs: {
      "acme/webapp#7": { state: "OPEN", headRefName: "feature" },
    },
  });

  await chromeMessageCheck();

  assert.equal(world.grouped.at(-1)?.title, "Finder");

  owners = { "acme/webapp:feature": "Agent::Tooling" };
  await chromeMessageCheck();

  assert.equal(world.grouped.at(-1)?.title, "Agent::Tooling");

  owners = {};
  await chromeMessageCheck();

  assert.equal(world.grouped.at(-1)?.title, "webapp");
});

test("a restarted worker restores ownership before grouping persisted PR metadata", async () => {
  world.tabs = [{ id: 1, url: `${PR}/7`, windowId: 1, groupId: -1 }];
  world.store = {
    owners: { "acme/webapp:feature": "Finder" },
    meta: {
      "acme/webapp#7": { state: "OPEN", title: "Feature", headRefName: "feature" },
    },
  };

  world.grouped = [];
  await freshWorker("ownership-restart");
  await world.installedListener();

  assert.equal(world.grouped.at(-1)?.title, "Finder");
});

// Holds the first owners read until release, signalling reading when it starts.
function holdOwnersRead() {
  const get = fakeChrome.storage.local.get;
  const { promise: held, resolve: release } = Promise.withResolvers<void>();
  const { promise: reading, resolve: signalRead } = Promise.withResolvers<void>();
  let intercepted = false;

  fakeChrome.storage.local.get = async (key) => {
    const snapshot = await get(key);

    if (key === "owners" && !intercepted) {
      intercepted = true;
      signalRead();
      await held;
    }

    return snapshot;
  };

  function restore() {
    release();
    fakeChrome.storage.local.get = get;
  }

  return { reading, release, restore };
}

// Resolves fresh once owners naming title are written.
function watchOwnersWrite(title: string) {
  const set = fakeChrome.storage.local.set;
  const { promise: fresh, resolve: signalFresh } = Promise.withResolvers<void>();

  fakeChrome.storage.local.set = async (values) => {
    await set(values);

    if (values.owners?.["acme/webapp:feature"] === title) {
      signalFresh();
    }
  };

  function restore() {
    fakeChrome.storage.local.set = set;
  }

  return { fresh, restore };
}

const OPEN_FEATURE: Meta = {
  "acme/webapp#7": { state: "OPEN", title: "Feature", headRefName: "feature" },
};

test("a delayed persisted restore cannot overwrite fresh ownership from a check", async () => {
  world.tabs = [{ id: 1, url: `${PR}/7`, windowId: 1, groupId: -1 }];
  world.store = { owners: { "acme/webapp:feature": "Old" }, meta: OPEN_FEATURE };

  const read = holdOwnersRead();
  const write = watchOwnersWrite("Fresh");

  world.nativeHandler = async () => ({
    ok: true,
    owners: { "acme/webapp:feature": "Fresh" },
    prs: OPEN_FEATURE,
  });

  try {
    const worker = await freshWorker("ownership-restore-race");
    const starting = world.installedListener();

    await read.reading;

    const checking = worker.checkNow();

    await write.fresh;
    read.release();
    await Promise.all([starting, checking]);

    assert.equal(world.grouped.at(-1)?.title, "Fresh");
  } finally {
    read.restore();
    write.restore();
  }
});

test("failed scans preserve the last good map for checks and Open all, including after restart", async () => {
  const good = { "acme/webapp:feature": "Finder" };

  world.tabs = [{ id: 1, url: `${PR}/7`, windowId: 1, groupId: -1 }];
  world.store = {
    owners: good,
    meta: { "acme/webapp#7": { state: "OPEN", headRefName: "feature" } },
  };

  world.grouped = [];
  world.nativeHandler = async (message) =>
    message.type === "states"
      ? {
          ok: true,
          owners: null,
          prs: { "acme/webapp#7": { state: "OPEN", headRefName: "feature" } },
        }
      : {
          ok: true,
          owners: null,
          total: 1,
          prs: [{ url: `${PR}/8`, headRefName: "feature" }],
          hasNextPage: false,
        };
  const worker = await freshWorker("failed-ownership-scan");

  await worker.checkNow();

  assert.equal(world.grouped.at(-1)?.title, "Finder");
  assert.deepEqual(world.store.owners, good);

  await worker.openAll();

  assert.equal(world.grouped.at(-1)?.title, "Finder");
  assert.deepEqual(world.store.owners, good);
});

const REPOS = ["webapp", "api", "docs"];

async function multiRepoWorker(tag: string) {
  world.tabs = [];
  world.store = {};
  world.hostSettings = { ...CONFIGURED, repos: REPOS };

  return freshWorker(tag);
}

test("the dropdown lists the repos and selects the remembered listed pick", async () => {
  const multi = await multiRepoWorker("multi-repo-choices");

  assert.deepEqual(await multi.repoChoices(), { repos: REPOS, selected: "webapp", show: true });

  await multi.pickRepo("api");

  assert.equal(world.store.prTabsRepo, "api");
  assert.deepEqual(await multi.repoChoices(), { repos: REPOS, selected: "api", show: true });

  await multi.pickRepo("docs");

  assert.equal(world.store.prTabsRepo, "docs");

  await multi.pickRepo("unlisted");

  assert.equal(world.store.prTabsRepo, "docs");
});

test("goTo opens a bare number in the picked repo, else the remembered pick", async () => {
  const multi = await multiRepoWorker("multi-repo");

  assert.deepEqual(await multi.goTo("7", "api"), { opened: "acme/api#7" });

  await multi.pickRepo("api");

  const stored = await multi.goTo("11");

  assert.deepEqual(stored, { opened: "acme/api#11" }, "no pick sent: the stored pick wins");
  assert.deepEqual(await multi.goTo("docs#8", "api"), { opened: "acme/docs#8" });
  assert.deepEqual(await multi.goTo("9", "unlisted"), { opened: "acme/webapp#9" });
  assert.equal(world.store.prTabsRepo, "api");
  assert.deepEqual(await multi.goTo("other#1"), { error: "Not a acme PR number or URL" });

  world.store = {};
  world.tabs = [];
});

test("one listed repo hides the dropdown", async () => {
  world.hostSettings = { ...CONFIGURED, repos: ["webapp"] };
  const single = await freshWorker("single-repo");
  assert.equal((await single.repoChoices()).show, false);
});

test("a pane lookup is one host request for at most the cap; none for no heads", async () => {
  const sent: HostRequest[] = [];

  world.nativeHandler = async (message) => {
    sent.push(message);

    return { ok: true, panes: { "acme/webapp:b0": { state: "working" } } };
  };

  const heads = Array.from({ length: MAX_PANE_LOOKUPS + 3 }, (_, i) => ({
    repo: "acme/webapp",
    branch: `b${i}`,
    extra: "dropped",
  }));

  assert.deepEqual(await panes([]), {});
  assert.deepEqual(await panes(heads), { "acme/webapp:b0": { state: "working" } });
  assert.equal(sent.length, 1);
  assert.equal(sent[0]?.type, "panes");
  assert.deepEqual(sent[0]?.type === "panes" ? sent[0].prs.at(0) : null, {
    repo: "acme/webapp",
    branch: "b0",
  });
  assert.equal(sent[0]?.type === "panes" ? sent[0].prs.length : 0, MAX_PANE_LOOKUPS);
});

test("focusing a pane sends repo and branch; a host error rejects with its text", async () => {
  const sent: HostRequest[] = [];
  const info = vi.spyOn(console, "info");
  const warn = vi.spyOn(console, "warn");

  world.nativeHandler = async (message) => {
    sent.push(message);

    return { ok: true, pane: "w1:p2" };
  };

  assert.deepEqual(await focusPane({ repo: "acme/webapp", branch: "feature" }), { pane: "w1:p2" });
  assert.deepEqual(sent, [{ type: "focusPane", repo: "acme/webapp", branch: "feature" }]);

  world.nativeHandler = async () => ({
    ok: false,
    error: "no Herdr pane has acme/webapp:x checked out",
  });

  await assert.rejects(focusPane({ repo: "acme/webapp", branch: "x" }), /no Herdr pane/);

  assert.equal(info.mock.calls.filter((call) => call[0] === "PR tabs focusPane send").length, 2);
  assert.equal(info.mock.calls.filter((call) => call[0] === "PR tabs focusPane reply").length, 1);
  assert.equal(warn.mock.calls[0]?.[0], "PR tabs focusPane failed");
});

test("Open reviews requires only the organization setting", async () => {
  world.hostSettings = { ...CONFIGURED, org: "", openAll: "" };
  const worker = await freshWorker("review-org-hint");

  assert.deepEqual(await worker.openReviews(), { error: "Set pr_tabs.org in orchestration.toml" });
  assert.deepEqual(await worker.openAll(), {
    error: "Set pr_tabs.org and pr_tabs.open_all in orchestration.toml",
  });
});
