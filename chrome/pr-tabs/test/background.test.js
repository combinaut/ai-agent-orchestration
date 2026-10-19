import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const noop = { addListener() {} };
const matches = (pattern, url) =>
  new RegExp(`^${pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*")}$`).test(url);

let tabs = [];
let nativeHandler;
let settingsCalls = 0;
let grouped = [];
let createdAlarms = [];
let alarmListener;
let installedListener;
let hostSettings = {
  ok: true,
  org: "acme",
  defaultRepo: "webapp",
  openAll: "is:pr is:open author:tester org:acme",
};
let nextId = 100;
let store = {};

globalThis.fetch = async () => ({ json: async () => JSON.parse(readFileSync(new URL("../extension/rules.json", import.meta.url))) });
globalThis.chrome = {
  tabs: {
    query: async ({ url, windowType }) =>
      tabs.filter((tab) => matches(url, tab.url) && (!windowType || (tab.windowType ?? "normal") === windowType)),
    get: async (id) => tabs.find((tab) => tab.id === id),
    create: async ({ url }) => {
      const tab = { id: nextId++, url, windowId: 1, groupId: -1, title: "" };
      tabs.push(tab);
      return tab;
    },
    update: async (id, props) => Object.assign(tabs.find((tab) => tab.id === id) || {}, props),
    remove: async (id) => {
      tabs = tabs.filter((tab) => tab.id !== id);
    },
    group: async () => 1,
    onUpdated: noop,
    onRemoved: noop,
  },
  tabGroups: { TAB_GROUP_ID_NONE: -1, query: async () => [], update: async (_id, properties) => { grouped.push(properties); }, get: async () => ({}) },
  windows: { getLastFocused: async () => ({ id: 1 }), update: async () => {} },
  alarms: {
    onAlarm: { addListener(listener) { alarmListener = listener; } },
    get: async () => null,
    create: async (name, options) => { createdAlarms.push({ name, options }); },
    clear: async () => {},
  },
  action: { setBadgeText: async () => {} },
  storage: {
    local: {
      get: async (defaults) => ({ ...defaults, ...store }),
      set: async (values) => Object.assign(store, values),
    },
  },
  runtime: {
    onMessage: noop,
    onStartup: noop,
    onInstalled: { addListener(listener) { installedListener = listener; } },
    getURL: (path) => path,
    lastError: undefined,
    sendNativeMessage: (_host, message, callback) =>
      (message.type === "settings"
        ? (settingsCalls++, Promise.resolve(hostSettings))
        : nativeHandler(message)
      ).then(callback),
  },
};
const { closeDuplicates, goTo, openAll, prEntries, checkNow: chromeMessageCheck } = await import("../extension/background.js");

test("PR tabs whose URL differs from the org in case are kept; other orgs are dropped", async () => {
  const configured = hostSettings;
  try {
    hostSettings = { ...configured, org: "" };
    const noOrg = await import("../extension/background.js?missing-org");
    assert.deepEqual(await noOrg.goTo("7"), {
      error: "Set pr_tabs.org in orchestration.toml",
    });
    hostSettings = { ...configured, defaultRepo: "" };
    const noRepo = await import("../extension/background.js?missing-repo");
    assert.deepEqual(await noRepo.goTo("7"), {
      error: "Set pr_tabs.default_repo in orchestration.toml",
    });
  } finally {
    hostSettings = configured;
  }
  const realNow = Date.now;
  let now = realNow();
  Date.now = () => now;
  try {
    tabs = []; store = {}; grouped = []; settingsCalls = 0;
    hostSettings = { ...configured, defaultRepo: "cached" };
    const seed = await import("../extension/background.js?cache-seed");
    await seed.goTo("1");
    assert.deepEqual(store.prTabsSettings, hostSettings);
    const good = store.prTabsSettings;
    hostSettings = { ok: false, error: "host unavailable" };
    const offline = await import("../extension/background.js?cache-offline");
    const before = settingsCalls;
    await offline.goTo("2");
    assert.equal(grouped.at(-1).title, "cached");
    for (let i = 0; i < 5; i++) await offline.goTo("2");
    assert.equal(settingsCalls, before + 1, "failed host retries are backed off");
    assert.deepEqual(store.prTabsSettings, good);
    now += 60001;
    hostSettings = { ...configured, defaultRepo: "docs" };
    await offline.goTo("3");
    assert.equal(settingsCalls, before + 2);
    assert.equal(store.prTabsSettings.defaultRepo, "docs");
    assert.ok(tabs.some(tab => tab.url === "https://github.com/acme/docs/pull/3"));
    store = {}; hostSettings = { ok: false, error: "host unavailable" };
    const empty = await import("../extension/background.js?cache-empty");
    const initial = settingsCalls;
    for (let i = 0; i < 5; i++) await assert.rejects(empty.goTo("4"), /host unavailable/);
    assert.equal(settingsCalls, initial + 1, "even a missing cache backs off retries");
    now += 60001; hostSettings = configured;
    await empty.goTo("4");
    assert.equal(settingsCalls, initial + 2);
  } finally {
    Date.now = realNow; hostSettings = configured; store = {}; tabs = [];
  }
  tabs = [
    { id: 1, url: "https://github.com/Acme/webapp/pull/1" },
    { id: 2, url: "https://github.com/acme/webapp/pull/2" },
    { id: 3, url: "https://github.com/other/webapp/pull/3" },
  ];
  const entries = await prEntries({ org: "acme" });
  assert.deepEqual(entries.map(({ tab }) => tab.id), [1, 2]);
});

test("a no-cache settings failure arms a retry that recovers after backoff", async () => {
  const configured = hostSettings;
  const realNow = Date.now;
  let now = realNow();
  Date.now = () => now;
  try {
    tabs = []; store = {}; createdAlarms = []; settingsCalls = 0;
    hostSettings = { ok: false, error: "host unavailable" };
    await import("../extension/background.js?settings-retry-alarm");
    await installedListener();
    assert.equal(createdAlarms.at(-1).options.delayInMinutes, 1);
    now += 60001;
    hostSettings = { ...configured };
    alarmListener({ name: "pr-tabs-poll" });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(settingsCalls, 2);
  } finally {
    Date.now = realNow; hostSettings = configured; store = {}; tabs = [];
  }
});

test("a PR opened by goTo while Open all searches is not opened a second time", async () => {
  tabs = [];
  store = {};
  const url = "https://github.com/acme/webapp/pull/7";
  nativeHandler = async () => {
    await goTo("7");
    return { ok: true, total: 1, prs: [{ url, title: "t", headRefName: "b" }], hasNextPage: false };
  };
  await openAll();
  assert.equal(tabs.filter((tab) => tab.url === url).length, 1);
});

const PR = "https://github.com/acme/webapp/pull";
const ids = () => tabs.map((tab) => tab.id);

test("goTo focuses an open tab of the same PR on another sub-page and opens no second one", async () => {
  tabs = [{ id: 1, url: `${PR}/7/files?w=1#diff`, windowId: 1, groupId: -1 }];
  assert.deepEqual(await goTo("https://GitHub.com/Acme/Webapp/pull/7"), { focused: "acme/webapp#7" });
  assert.deepEqual(ids(), [1]);
  assert.equal(tabs[0].active, true);
});

test("Open all skips a PR that is open on a sub-page", async () => {
  tabs = [{ id: 1, url: `${PR}/7/commits`, windowId: 1, groupId: -1 }];
  store = {};
  nativeHandler = async () => ({ ok: true, total: 1, prs: [{ url: `${PR}/7`, title: "t", headRefName: "b" }], hasNextPage: false });
  assert.equal((await openAll()).opened, 0);
  assert.deepEqual(ids(), [1]);
});

test("Close duplicate tabs closes the extras of a PR across windows and reports the count", async () => {
  tabs = [
    { id: 5, url: `${PR}/7`, windowId: 2 },
    { id: 3, url: `${PR}/7/files`, windowId: 1 },
    { id: 4, url: `${PR}/7/checks#x`, windowId: 1 },
  ];
  assert.deepEqual(await closeDuplicates(), { closed: 2 });
  assert.deepEqual(ids(), [3]);
});

test("Close duplicate tabs keeps the active tab, even a newer one", async () => {
  tabs = [
    { id: 3, url: `${PR}/7`, windowId: 1 },
    { id: 9, url: `${PR}/7/files`, windowId: 1, active: true },
  ];
  await closeDuplicates();
  assert.deepEqual(ids(), [9]);
});

test("Close duplicate tabs leaves other PRs, non-PR tabs and incognito tabs alone", async () => {
  tabs = [
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
  tabs = [{ id: 1, url: `${PR}/7`, windowId: 1, groupId: -1, incognito: true }];
  assert.deepEqual(await goTo("7"), { opened: "acme/webapp#7" });
});

test("Close duplicate tabs ignores tabs outside normal windows", async () => {
  tabs = [
    { id: 1, url: `${PR}/7` },
    { id: 2, url: `${PR}/7`, windowType: "popup" },
    { id: 3, url: `${PR}/7`, windowType: "app" },
  ];
  assert.deepEqual(await closeDuplicates(), { closed: 0 });
  assert.deepEqual(ids(), [1, 2, 3]);
});

// Runs change() when closeDuplicates re-reads the tabs for the first time after the initial scan.
function changeAfterScan(change) {
  const query = chrome.tabs.query;
  let calls = 0;
  chrome.tabs.query = async (args) => {
    if (++calls === 2) change();
    return query(args);
  };
  return () => {
    chrome.tabs.query = query;
  };
}

test("Close duplicate tabs does not close a tab that is no longer a duplicate", async () => {
  tabs = [
    { id: 1, url: `${PR}/7` },
    { id: 2, url: `${PR}/7` },
  ];
  const restore = changeAfterScan(() => {
    tabs[0].url = `${PR}/8`;
  });
  try {
    assert.deepEqual(await closeDuplicates(), { closed: 0 });
  } finally {
    restore();
  }
  assert.deepEqual(ids(), [1, 2]);
});

test("Close duplicate tabs does not close a tab that became active meanwhile", async () => {
  tabs = [
    { id: 1, url: `${PR}/7` },
    { id: 2, url: `${PR}/7` },
  ];
  const restore = changeAfterScan(() => {
    tabs[1].active = true;
  });
  try {
    assert.deepEqual(await closeDuplicates(), { closed: 0 });
  } finally {
    restore();
  }
  assert.deepEqual(ids(), [1, 2]);
});

test("Close duplicate tabs does not close the last tab when the kept one is gone", async () => {
  tabs = [
    { id: 1, url: `${PR}/7` },
    { id: 2, url: `${PR}/7` },
    { id: 3, url: `${PR}/7` },
  ];
  const restore = changeAfterScan(() => {
    tabs = tabs.filter((tab) => tab.id !== 1);
  });
  try {
    await closeDuplicates();
  } finally {
    restore();
  }
  assert.deepEqual(ids(), [2]);
});

test("Close duplicate tabs does not close a tab that moved to another duplicated PR", async () => {
  tabs = [
    { id: 5, url: `${PR}/7` },
    { id: 6, url: `${PR}/7` },
    { id: 3, url: `${PR}/8` },
  ];
  const restore = changeAfterScan(() => {
    tabs[1].url = `${PR}/8`;
  });
  try {
    await closeDuplicates();
  } finally {
    restore();
  }
  assert.deepEqual(ids(), [5, 6, 3]);
});

test("Close duplicate tabs keeps the active tab of the focused window (window 1)", async () => {
  tabs = [
    { id: 1, url: `${PR}/7`, windowId: 2, active: true },
    { id: 2, url: `${PR}/7`, windowId: 1, active: true },
  ];
  await closeDuplicates();
  assert.deepEqual(ids(), [2]);
});


test("existing check cadence regroups tabs after ownership changes or disappears", async () => {
  tabs = [{ id: 1, url: `${PR}/7`, windowId: 1, groupId: -1 }];
  store = {}; grouped = [];
  let owners = { "acme/webapp:feature": "Finder" };
  nativeHandler = async () => ({ ok: true, owners, prs: {
    "acme/webapp#7": { state: "OPEN", headRefName: "feature" },
  } });
  await chromeMessageCheck();
  assert.equal(grouped.at(-1).title, "Finder");
  owners = { "acme/webapp:feature": "Agent::Tooling" };
  await chromeMessageCheck();
  assert.equal(grouped.at(-1).title, "Agent::Tooling");
  owners = {};
  await chromeMessageCheck();
  assert.equal(grouped.at(-1).title, "webapp");
});

test("a restarted worker restores ownership before grouping persisted PR metadata", async () => {
  tabs = [{ id: 1, url: `${PR}/7`, windowId: 1, groupId: -1 }];
  store = { owners: { "acme/webapp:feature": "Finder" }, meta: {
    "acme/webapp#7": { state: "OPEN", headRefName: "feature" },
  } };
  grouped = [];
  await import("../extension/background.js?ownership-restart");
  await installedListener();
  assert.equal(grouped.at(-1).title, "Finder");
});

test("a delayed persisted restore cannot overwrite fresh ownership from a check", async () => {
  tabs = [{ id: 1, url: `${PR}/7`, windowId: 1, groupId: -1 }];
  store = { owners: { "acme/webapp:feature": "Old" }, meta: {
    "acme/webapp#7": { state: "OPEN", headRefName: "feature" },
  } };
  const get = chrome.storage.local.get;
  const set = chrome.storage.local.set;
  let release, signalRead, signalFresh;
  const held = new Promise(resolve => { release = resolve; });
  const reading = new Promise(resolve => { signalRead = resolve; });
  const fresh = new Promise(resolve => { signalFresh = resolve; });
  let intercepted = false;
  chrome.storage.local.get = async defaults => {
    const snapshot = await get(defaults);
    if ("owners" in defaults && !intercepted) {
      intercepted = true;
      signalRead();
      await held;
    }
    return snapshot;
  };
  chrome.storage.local.set = async values => {
    await set(values);
    if (values.owners?.["acme/webapp:feature"] === "Fresh") signalFresh();
  };
  nativeHandler = async () => ({ ok: true, owners: { "acme/webapp:feature": "Fresh" }, prs: {
    "acme/webapp#7": { state: "OPEN", headRefName: "feature" },
  } });
  try {
    const worker = await import("../extension/background.js?ownership-restore-race");
    const starting = installedListener();
    await reading;
    const checking = worker.checkNow();
    await fresh;
    release();
    await Promise.all([starting, checking]);
    assert.equal(grouped.at(-1).title, "Fresh");
  } finally {
    release();
    chrome.storage.local.get = get;
    chrome.storage.local.set = set;
  }
});
