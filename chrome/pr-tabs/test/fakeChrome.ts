import { readFileSync } from "node:fs";
import { createI18n } from "#test/i18n/catalog";
import type { HostOk, HostRequest, Rules } from "#/types";
import type {
  FakeAction,
  FakeAlarms,
  FakeChrome,
  FakeRuntime,
  FakeStorage,
  FakeTab,
  FakeTabGroups,
  FakeTabs,
  FakeWindows,
  World,
} from "#test/types";

export const CONFIGURED: Readonly<HostOk<Partial<Rules>>> = {
  ok: true,
  org: "acme",
  defaultRepo: "webapp",
  openAll: "is:pr is:open author:tester org:acme",
};

async function noHostAnswer(): Promise<unknown> {
  throw new Error("no native host answer set");
}

function noListener(): never {
  throw new Error("listener not registered");
}

// What the fake browser holds; tests set and read it directly.
export const world: World = {
  tabs: [],
  created: [],
  store: {},
  grouped: [],
  createdAlarms: [],
  settingsCalls: 0,
  hostSettings: CONFIGURED,
  nativeHandler: noHostAnswer,
  nextId: 100,
  alarmListener: noListener,
  installedListener: noListener,
  startupListener: noListener,
};

const ignored = { addListener() {} };

function matches(pattern: string, url: string): boolean {
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");

  return new RegExp(`^${escaped}$`).test(url);
}

function matchesQuery(tab: FakeTab, query: chrome.tabs.QueryInfo): boolean {
  const patterns = [query.url ?? "*"].flat();
  const inWindowType = !query.windowType || (tab.windowType ?? "normal") === query.windowType;

  return inWindowType && patterns.some((pattern) => matches(pattern, tab.url));
}

function hostAnswer(message: HostRequest): Promise<unknown> {
  if (message.type !== "settings") {
    return world.nativeHandler(message);
  }

  world.settingsCalls++;

  return Promise.resolve(world.hostSettings);
}

function createTab(properties: chrome.tabs.CreateProperties): FakeTab {
  const { url = "", active = true } = properties;
  const tab = { id: world.nextId++, url, active, windowId: 1, groupId: -1, title: "" };

  world.created.push(properties);
  world.tabs.push(tab);

  return tab;
}

const tabs = {
  query: async (query) => world.tabs.filter((tab) => matchesQuery(tab, query)),
  get: async (id) => world.tabs.find((tab) => tab.id === id),
  create: async (properties) => createTab(properties),
  update: async (id, properties) =>
    Object.assign(world.tabs.find((tab) => tab.id === id) ?? { id, url: "" }, properties),
  remove: async (id) => {
    world.tabs = world.tabs.filter((tab) => tab.id !== id);
  },
  group: async () => 1,
  onUpdated: ignored,
  onRemoved: ignored,
} satisfies FakeTabs;

const tabGroups = {
  TAB_GROUP_ID_NONE: -1,
  query: async () => [],
  update: async (_id, properties) => {
    world.grouped.push(properties);
  },
  get: async () => ({}),
} satisfies FakeTabGroups;

function handleAlarmListen(listener: World["alarmListener"]): void {
  world.alarmListener = listener;
}

const alarms = {
  onAlarm: { addListener: handleAlarmListen },
  get: async () => undefined,
  create: async (name, options) => {
    world.createdAlarms.push({ name, options });
  },
  clear: async () => true,
} satisfies FakeAlarms;

const storage = {
  local: {
    get: async (key) => ({ [key]: world.store[key] }),
    set: async (values) => {
      Object.assign(world.store, values);
    },
  },
} satisfies FakeStorage;

function handleInstalledListen(listener: World["installedListener"]): void {
  world.installedListener = listener;
}

function handleStartupListen(listener: World["startupListener"]): void {
  world.startupListener = listener;
}

const runtime = {
  onMessage: ignored,
  onStartup: { addListener: handleStartupListen },
  onInstalled: { addListener: handleInstalledListen },
  getURL: (path) => path,
  lastError: undefined,
  sendNativeMessage(_host, message, callback) {
    void hostAnswer(message).then(callback);
  },
} satisfies FakeRuntime;

const windows = {
  getLastFocused: async () => ({ id: 1 }),
  update: async () => {},
} satisfies FakeWindows;

const action: FakeAction = { setBadgeText: async () => {} };

export const fakeChrome = {
  i18n: createI18n("en"),
  tabs,
  tabGroups,
  windows,
  alarms,
  action,
  storage,
  runtime,
} satisfies FakeChrome;

async function readRules(): Promise<unknown> {
  return JSON.parse(readFileSync(new URL("../public/rules.json", import.meta.url), "utf8"));
}

// Installs the stub as chrome and serves rules.json to fetch; it covers only what background.ts
// calls.
export function installFakeChrome(): void {
  globalThis.chrome = fakeChrome as unknown as typeof chrome;
  globalThis.fetch = (async () => ({ json: readRules })) as unknown as typeof fetch;
}
