// Every test-only type: the fake browser, fake service worker, host runs and template AST.
import type { mount } from "@vue/test-utils";
import type { Component } from "vue";
import type { SFCDescriptor } from "vue/compiler-sfc";
import type {
  Ask,
  CatalogEntry,
  GroupId,
  HostReply,
  HostRequest,
  PlainRequest,
  Replies,
  Reply,
  Request,
  RequestType,
  Rules,
  StoredState,
  Tab,
  TabActionType,
  TabId,
  WindowId,
} from "#/types";

// The fake browser.
export type FakeTab = Required<Pick<Tab, "id" | "url">> &
  Partial<Pick<Tab, "windowId" | "groupId" | "title" | "active" | "incognito">> &
  Pick<chrome.tabs.QueryInfo, "windowType">;

export type FakeStore = Partial<StoredState>;
export type HostSettings = HostReply<Partial<Rules>>;

export interface CreatedAlarm {
  name: string;
  options: chrome.alarms.AlarmCreateInfo;
}

export type FiredAlarm = Pick<chrome.alarms.Alarm, "name">;

export interface World {
  tabs: FakeTab[];
  created: chrome.tabs.CreateProperties[];
  store: FakeStore;
  grouped: chrome.tabGroups.UpdateProperties[];
  createdAlarms: CreatedAlarm[];
  settingsCalls: number;
  hostSettings: HostSettings;
  nativeHandler: (message: HostRequest) => Promise<unknown>;
  nextId: TabId;
  alarmListener: (alarm: FiredAlarm) => void;
  installedListener: () => Promise<void>;
}

// A fake namespace may only name members its chrome namespace has.
type Known<Fake, Real> = Exclude<keyof Fake, keyof Real> extends never ? Fake : never;
type Listened<E extends { addListener: unknown }> = Pick<E, "addListener">;

export type FakeTabs = Known<
  {
    query: (query: chrome.tabs.QueryInfo) => Promise<FakeTab[]>;
    get: (id: TabId) => Promise<FakeTab | undefined>;
    create: (properties: chrome.tabs.CreateProperties) => Promise<FakeTab>;
    update: (id: TabId, properties: chrome.tabs.UpdateProperties) => Promise<FakeTab>;
    remove: (id: TabId) => Promise<void>;
    group: (options: chrome.tabs.GroupOptions) => Promise<GroupId>;
    onUpdated: Listened<typeof chrome.tabs.onUpdated>;
    onRemoved: Listened<typeof chrome.tabs.onRemoved>;
  },
  typeof chrome.tabs
>;

export type FakeTabGroups = Known<
  {
    TAB_GROUP_ID_NONE: typeof chrome.tabGroups.TAB_GROUP_ID_NONE;
    query: (query: chrome.tabGroups.QueryInfo) => Promise<chrome.tabGroups.TabGroup[]>;
    update: (id: GroupId, properties: chrome.tabGroups.UpdateProperties) => Promise<void>;
    get: (id: GroupId) => Promise<Partial<chrome.tabGroups.TabGroup>>;
  },
  typeof chrome.tabGroups
>;

export type FakeAlarms = Known<
  {
    onAlarm: { addListener: (listener: World["alarmListener"]) => void };
    get: (name: string) => Promise<chrome.alarms.Alarm | undefined>;
    create: (name: string, options: chrome.alarms.AlarmCreateInfo) => Promise<void>;
    clear: (name: string) => Promise<boolean>;
  },
  typeof chrome.alarms
>;

export type FakeStorage = Known<
  {
    local: {
      get: (key: keyof StoredState) => Promise<FakeStore>;
      set: (values: FakeStore) => Promise<void>;
    };
  },
  typeof chrome.storage
>;

export type FakeRuntime = Known<
  {
    onMessage: Listened<typeof chrome.runtime.onMessage>;
    onStartup: Listened<typeof chrome.runtime.onStartup>;
    onInstalled: { addListener: (listener: World["installedListener"]) => void };
    getURL: (path: string) => string;
    lastError: typeof chrome.runtime.lastError;
    sendNativeMessage: (
      host: string,
      message: HostRequest,
      callback: (reply: unknown) => void,
    ) => void;
  },
  typeof chrome.runtime
>;

export type FakeWindows = Known<
  {
    getLastFocused: () => Promise<Pick<chrome.windows.Window, "id">>;
    update: (id: WindowId, info: chrome.windows.UpdateInfo) => Promise<void>;
  },
  typeof chrome.windows
>;

export type FakeAction = Known<
  { setBadgeText: (details: chrome.action.BadgeTextDetails) => Promise<void> },
  typeof chrome.action
>;

export type FakeI18n = Known<
  {
    getMessage: (key: string, substitutions?: string | string[]) => string;
    getUILanguage: () => string;
  },
  typeof chrome.i18n
>;

export type FakeChrome = Known<
  {
    i18n: FakeI18n;
    tabs: FakeTabs;
    tabGroups: FakeTabGroups;
    windows: FakeWindows;
    alarms: FakeAlarms;
    action: FakeAction;
    storage: FakeStorage;
    runtime: FakeRuntime;
  },
  typeof chrome
>;

// The locale catalogs.
export type Locale = "de" | "en" | "es" | "fr" | "pt";
export type LocaleCatalog = Readonly<Record<string, CatalogEntry>>;

// The fake service worker.
export type FakeReplies = { [K in RequestType]?: Reply<K> };

export interface FakeAsk {
  ask: Ask;
  sent: Request[];
}

export interface ActionCase {
  button: string;
  request: PlainRequest<TabActionType>;
  reply: Replies[TabActionType];
  status: string;
}

export type PopupWrapper<C extends Component> = ReturnType<typeof mount<C, C>>;

// Native host runs.
export interface HostAnswer {
  ok?: boolean;
  error?: string;
  [field: string]: unknown;
}

export interface FakeGh {
  reply?: unknown;
  status?: number;
  stderr?: string;
  raw?: Buffer;
}

// The template AST of vue/compiler-sfc.
export type TemplateRoot = NonNullable<NonNullable<SFCDescriptor["template"]>["ast"]>;
export type TemplateNode = TemplateRoot | TemplateRoot["children"][number];
export type TemplateElement = Extract<TemplateNode, { tagType: unknown }>;
export type TemplateDirective = Extract<TemplateElement["props"][number], { modifiers: unknown }>;
