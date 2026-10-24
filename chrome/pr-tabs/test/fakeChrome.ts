import { readFileSync } from "node:fs";

export interface FakeTab {
  id: number;
  url: string;
  windowId?: number;
  groupId?: number;
  title?: string;
  active?: boolean;
  incognito?: boolean;
  windowType?: string;
}

export interface Store {
  prTabsSettings?: Record<string, unknown>;
  owners?: Record<string, string>;
  meta?: Record<string, { state?: string; title?: string; headRefName?: string }>;
  failures?: number;
  cursor?: number;
  prTabsRepo?: string;
}

interface GroupProps {
  title?: string;
  color?: string;
}

interface CreatedAlarm {
  name: string;
  options: chrome.alarms.AlarmCreateInfo;
}

type FiredAlarm = Pick<chrome.alarms.Alarm, "name">;

interface FakeQuery {
  url: string;
  windowType?: string;
}

interface FakeCreate {
  url: string;
}

export interface HostMessage {
  type: string;
}

interface World {
  tabs: FakeTab[];
  store: Store;
  grouped: GroupProps[];
  createdAlarms: CreatedAlarm[];
  settingsCalls: number;
  hostSettings: Record<string, unknown>;
  nativeHandler: (message: HostMessage) => Promise<unknown>;
  nextId: number;
  alarmListener: (alarm: FiredAlarm) => void;
  installedListener: () => Promise<void>;
}

export const CONFIGURED: Readonly<Record<string, unknown>> = {
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
  store: {},
  grouped: [],
  createdAlarms: [],
  settingsCalls: 0,
  hostSettings: CONFIGURED,
  nativeHandler: noHostAnswer,
  nextId: 100,
  alarmListener: noListener,
  installedListener: noListener,
};

const ignored = { addListener() {} };

function matches(pattern: string, url: string): boolean {
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
  return new RegExp(`^${escaped}$`).test(url);
}

function inWindowType(tab: FakeTab, windowType: string | undefined): boolean {
  return !windowType || (tab.windowType ?? "normal") === windowType;
}

function hostAnswer(message: HostMessage): Promise<unknown> {
  if (message.type !== "settings") {
    return world.nativeHandler(message);
  }

  world.settingsCalls++;

  return Promise.resolve(world.hostSettings);
}

const tabs = {
  query: async (query: FakeQuery): Promise<FakeTab[]> =>
    world.tabs.filter((tab) => matches(query.url, tab.url) && inWindowType(tab, query.windowType)),
  get: async (id: number): Promise<FakeTab | undefined> => world.tabs.find((tab) => tab.id === id),
  create: async (properties: FakeCreate): Promise<FakeTab> => {
    const tab = { id: world.nextId++, url: properties.url, windowId: 1, groupId: -1, title: "" };
    world.tabs.push(tab);
    return tab;
  },
  update: async (id: number, props: Partial<FakeTab>) =>
    Object.assign(world.tabs.find((tab) => tab.id === id) ?? {}, props),
  remove: async (id: number) => {
    world.tabs = world.tabs.filter((tab) => tab.id !== id);
  },
  group: async () => 1,
  onUpdated: ignored,
  onRemoved: ignored,
};

const tabGroups = {
  TAB_GROUP_ID_NONE: -1,
  query: async (): Promise<unknown[]> => [],
  update: async (_id: number, properties: GroupProps) => {
    world.grouped.push(properties);
  },
  get: async () => ({}),
};

const alarms = {
  onAlarm: {
    addListener(listener: World["alarmListener"]) {
      world.alarmListener = listener;
    },
  },
  get: async () => null,
  create: async (name: string, options: CreatedAlarm["options"]) => {
    world.createdAlarms.push({ name, options });
  },
  clear: async () => {},
};

const storage = {
  local: {
    get: async (key: keyof Store): Promise<Store> => ({ [key]: world.store[key] }),
    set: async (values: Store): Promise<void> => {
      Object.assign(world.store, values);
    },
  },
};

const runtime = {
  onMessage: ignored,
  onStartup: ignored,
  onInstalled: {
    addListener(listener: World["installedListener"]) {
      world.installedListener = listener;
    },
  },
  getURL: (path: string) => path,
  lastError: undefined,
  sendNativeMessage(_host: string, message: HostMessage, callback: (reply: unknown) => void) {
    void hostAnswer(message).then(callback);
  },
};

// The chrome APIs background.ts calls, backed by world.
export const fakeChrome = {
  tabs,
  tabGroups,
  windows: { getLastFocused: async () => ({ id: 1 }), update: async () => {} },
  alarms,
  action: { setBadgeText: async () => {} },
  storage,
  runtime,
};

async function readRules(): Promise<unknown> {
  return JSON.parse(readFileSync(new URL("../public/rules.json", import.meta.url), "utf8"));
}

// Installs the stub as chrome and serves rules.json to fetch; it covers only what background.ts
// calls.
export function installFakeChrome(): void {
  globalThis.chrome = fakeChrome as unknown as typeof chrome;
  globalThis.fetch = (async () => ({ json: readRules })) as unknown as typeof fetch;
}
