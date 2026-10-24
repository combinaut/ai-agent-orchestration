import {
  duplicateTabIds,
  prKey,
  findOpenTab,
  tabUpdateMatters,
  nextDelaySeconds,
  parsePrUrl,
  parseTarget,
  planOpenAll,
  pollBatch,
  pruneMeta,
  recordStates,
  seedOpen,
  stillShowsPr,
  summarize,
  MAX_SEARCH_PAGES,
  OPEN_ALL_CAP,
  FIRST_POLL_SECONDS,
  tabsToClose,
  groupForPr,
  effectiveRepos,
  pickedRepo,
  showRepoPicker,
  type Closed,
  type Meta,
  type Owners,
  type Planned,
  type Pr,
  type PrState,
  type Rules,
  type SearchHit,
  type States,
} from "./core";
import { messageListener } from "./messages";

type Tab = chrome.tabs.Tab;
interface PrEntry {
  tab: Tab;
  pr: Pr;
}

interface HostReply {
  ok: boolean;
  error?: string;
  owners?: Owners | null;
  prs?: unknown;
  total?: number;
  hasNextPage?: boolean;
  endCursor?: string;
}
interface StatesReply extends HostReply {
  prs: States;
}
interface SearchReply extends HostReply {
  prs: SearchHit[];
  total: number;
}

const HOST = "orch.pr_tabs";
const ALARM = "pr-tabs-poll";
const HOST_TIMEOUT_MS = 45000;
const GROUP_DEBOUNCE_MS = 400;
const POLL_SOON_MS = 5000;
const SEARCH_LIMIT = 100;

const SETTINGS_RETRY_MS = 60000;
let owners: Owners | undefined;
let rulesPromise: Promise<Rules> | undefined;
let retryRulesAt = 0;
function loadRules(): Promise<Rules> {
  if (retryRulesAt && Date.now() >= retryRulesAt) {
    rulesPromise = undefined;
    retryRulesAt = 0;
  }
  return (rulesPromise ||= fetch(chrome.runtime.getURL("rules.json"))
    .then((response) => response.json() as Promise<Rules>)
    .then(async (rules) => {
      try {
        const settings = await askHost<HostReply & Partial<Rules>>({ type: "settings" });
        const configured = { ...rules, ...settings };
        await chrome.storage.local.set({ prTabsSettings: settings });
        return configured;
      } catch (error) {
        retryRulesAt = Date.now() + SETTINGS_RETRY_MS;
        const { prTabsSettings } = await chrome.storage.local.get<{ prTabsSettings: Partial<Rules> | null }>({
          prTabsSettings: null,
        });
        if (!prTabsSettings) {
          await chrome.alarms.create(ALARM, { delayInMinutes: SETTINGS_RETRY_MS / 60000 });
          throw error;
        }
        console.warn("PR tabs: using cached settings:", error);
        return { ...rules, ...prTabsSettings };
      }
    })
    .catch((error: unknown) => {
      retryRulesAt ||= Date.now() + SETTINGS_RETRY_MS;
      throw error;
    }));
}

// One native host process per request; the host exits after replying.
function askHost<T extends HostReply = HostReply>(message: object): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("native host timed out")), HOST_TIMEOUT_MS);
    chrome.runtime.sendNativeMessage(HOST, message, (reply?: T) => {
      clearTimeout(timer);
      if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
      else if (!reply?.ok) reject(new Error(reply?.error || "empty reply from native host"));
      else resolve(reply);
    });
  });
}

let chain: Promise<unknown> = Promise.resolve();
function serial<T>(task: () => Promise<T>): Promise<T> {
  const run = chain.then(task, task);
  chain = run.catch((error: unknown) => console.warn("PR tabs:", error));
  return run;
}

// Tab IDs that showed a PR at the last scan.
const prTabIds = new Set<number>();

// Normal-window github.com PR tabs; incognito tabs are left out.
async function openPrTabs(): Promise<Tab[]> {
  const tabs = await chrome.tabs.query({ url: "https://github.com/*/*/pull/*", windowType: "normal" });
  return tabs.filter((tab) => !tab.incognito);
}

export async function prEntries(rules: Pick<Rules, "org">): Promise<PrEntry[]> {
  const tabs = await openPrTabs();
  const entries = tabs
    .map((tab) => ({ tab, pr: parsePrUrl(tab.url, rules.org) }))
    .filter((entry): entry is PrEntry => !!entry.pr);
  prTabIds.clear();
  for (const { tab } of entries) prTabIds.add(tab.id!);
  return entries;
}

// null/absent ownership means discovery failed; an empty object is a good scan.
async function recordOwners(next: Owners | null | undefined): Promise<void> {
  if (!next || typeof next !== "object" || Array.isArray(next)) return;
  owners = next;
  await chrome.storage.local.set({ owners });
}

async function readMeta(): Promise<Meta> {
  return (await chrome.storage.local.get<{ meta: Meta }>({ meta: {} })).meta;
}

let metaChain: Promise<unknown> = Promise.resolve();
// Every metadata write runs here, one at a time: read, change, write.
function updateMeta(change: (meta: Meta) => Meta | Promise<Meta>): Promise<void> {
  const run = metaChain.then(async () => {
    const meta = await readMeta();
    await chrome.storage.local.set({ meta: await change(meta) });
  });
  metaChain = run.catch((error: unknown) => console.warn("PR tabs:", error));
  return run;
}

async function groupFor(windowId: number, groupInfo: { name: string }): Promise<chrome.tabGroups.TabGroup | null> {
  const [group] = await chrome.tabGroups.query({ windowId, title: groupInfo.name });
  return group || null;
}

// Moves the tab into its architect group; a tab already there is left alone.
async function groupTab(tab: Tab, pr: Pr, _rules: Rules, meta: Meta): Promise<void> {
  if (tab.pinned) return;
  if (owners === undefined) {
    const saved = await chrome.storage.local.get<{ owners: Owners }>({ owners: {} });
    // A host refresh can finish while storage is being read.
    owners ??= saved.owners;
  }
  const info: PrState = meta[pr.key] || {};
  const groupInfo = groupForPr(owners, { ...pr, branch: info.headRefName, headRepository: info.headRepository });
  const group = await groupFor(tab.windowId, groupInfo);
  if (group && group.color !== groupInfo.color) await chrome.tabGroups.update(group.id, { color: groupInfo.color });
  if (group && tab.groupId === group.id) return;
  if (group) {
    await chrome.tabs.group({ groupId: group.id, tabIds: [tab.id!] });
    return;
  }
  const groupId = await chrome.tabs.group({ tabIds: [tab.id!], createProperties: { windowId: tab.windowId } });
  await chrome.tabGroups.update(groupId, { title: groupInfo.name, color: groupInfo.color });
}

async function groupTabId(tabId: number): Promise<void> {
  const rules = await loadRules();
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  const pr = tab && parsePrUrl(tab.url, rules.org);
  if (!pr) return;
  const meta = await readMeta();
  await groupTab(tab, pr, rules, meta);
  if (!meta[pr.key]) pollSoon();
}

// Drops metadata of PRs without a tab.
function pruneClosedTabs(rules: Rules): Promise<void> {
  return updateMeta(async (meta) => pruneMeta(meta, new Set((await prEntries(rules)).map(({ pr }) => pr.key))));
}

async function ensureAlarm(): Promise<void> {
  const rules = await loadRules();
  await pruneClosedTabs(rules);
  const entries = await prEntries(rules);
  if (!entries.length) {
    await chrome.alarms.clear(ALARM);
    return;
  }
  if (!(await chrome.alarms.get(ALARM))) {
    await chrome.alarms.create(ALARM, { delayInMinutes: FIRST_POLL_SECONDS / 60 });
  }
}

let pollTimer: ReturnType<typeof setTimeout> | undefined;
function pollSoon(): void {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, POLL_SOON_MS);
}

type CheckReply = Awaited<ReturnType<typeof checkOnce>>;
let current: Promise<CheckReply> | null = null;
let pollAgain = false;
// One check at a time; a check requested during one runs once after it.
function poll(): Promise<CheckReply> {
  if (current) {
    pollAgain = true;
    return current;
  }
  current = checkOnce().finally(() => {
    current = null;
    if (pollAgain) {
      pollAgain = false;
      void poll();
    } else {
      ensureAlarm().catch((error: unknown) => console.warn("PR tabs:", error));
    }
  });
  return current;
}

export async function checkNow(): Promise<CheckReply> {
  if (current) await current;
  return poll();
}

// Fetches the PR states in one host call, closes background tabs of merged or closed PRs and reports what happened.
async function checkOnce() {
  const rules = await loadRules();
  const entries = await prEntries(rules);
  if (!entries.length) {
    await chrome.alarms.clear(ALARM);
    return { tabs: 0, prs: 0, checked: 0, closed: [], inView: [] };
  }
  const byKey = new Map(entries.map(({ pr }) => [pr.key, pr]));
  const stored = await chrome.storage.local.get<{ failures: number; cursor: number }>({ failures: 0, cursor: 0 });
  const { batch, nextCursor } = pollBatch([...byKey.keys()].toSorted(), stored.cursor);
  let reply: StatesReply;
  try {
    reply = await askHost<StatesReply>({
      type: "states",
      prs: batch.map((key) => {
        const { owner, repo, number } = byKey.get(key)!;
        return { owner, repo, number };
      }),
    });
  } catch (error) {
    const failures = stored.failures + 1;
    console.warn("PR tabs poll failed:", (error as Error).message);
    await chrome.storage.local.set({ failures });
    await chrome.alarms.create(ALARM, { delayInMinutes: nextDelaySeconds(failures) / 60 });
    return { error: `GitHub check failed: ${(error as Error).message}` };
  }
  await recordOwners(reply.owners);
  const keyById = new Map(entries.map(({ tab, pr }) => [tab.id!, pr.key]));
  const closed: Closed[] = [];
  for (const id of tabsToClose(entries, reply.prs)) {
    const tab = await chrome.tabs.get(id).catch(() => null);
    if (
      tab &&
      !tab.active &&
      stillShowsPr(tab, keyById.get(id), rules.org) &&
      (await chrome.tabs.remove(id).then(
        () => true,
        () => false,
      ))
    ) {
      closed.push({ key: keyById.get(id)!, state: reply.prs[keyById.get(id)!]!.state });
    }
  }
  const inView = entries
    .filter(({ tab, pr }) => tab.active && ["MERGED", "CLOSED"].includes(reply.prs[pr.key]?.state ?? ""))
    .map(({ pr }) => pr.key);
  await updateMeta((meta) => recordStates(meta, reply.prs));
  await chrome.storage.local.set({ failures: 0, cursor: nextCursor });
  await chrome.alarms.create(ALARM, { delayInMinutes: nextDelaySeconds(0) / 60 });
  const meta = await readMeta();
  await serial(async () => {
    for (const {
      tab: { id },
    } of entries) {
      const tab = await chrome.tabs.get(id!).catch(() => null);
      const pr = tab && parsePrUrl(tab.url, rules.org);
      if (pr) await groupTab(tab, pr, rules, meta).catch((error: unknown) => console.warn("PR tabs:", error));
    }
  });
  return { tabs: entries.length, prs: byKey.size, checked: batch.length, closed, inView };
}

// Re-checks the open tabs inside the serial chain, so a tab opened since the search is not duplicated.
function openPr({ hit, pr }: Planned, rules: Rules): Promise<boolean> {
  return serial(async () => {
    if ((await prEntries(rules)).some((entry) => entry.pr.key === pr.key)) return false;
    const tab = await chrome.tabs.create({ url: pr.url, active: false });
    await groupTab(tab, pr, rules, {
      [pr.key]: { title: hit.title, headRefName: hit.headRefName, headRepository: hit.headRepository },
    });
    return true;
  });
}

export async function openAll() {
  const rules = await loadRules();
  if (!rules.org || !rules.openAll)
    return {
      error: "Set pr_tabs.org and pr_tabs.open_all in orchestration.toml",
    };
  const entries = await prEntries(rules);
  const openKeys = new Set(entries.map(({ pr }) => pr.key));
  const search: { total: number; prs: SearchHit[] } = { total: 0, prs: [] };
  let after: string | undefined;
  for (let page = 0; page < MAX_SEARCH_PAGES; page++) {
    const reply = await askHost<SearchReply>({
      type: "search",
      query: rules.openAll,
      limit: SEARCH_LIMIT,
      ...(after && { after }),
    });
    await recordOwners(reply.owners);
    search.total = reply.total;
    search.prs.push(...reply.prs);
    if (!reply.hasNextPage || planOpenAll(search, openKeys, rules.org).open.length >= OPEN_ALL_CAP) break;
    after = reply.endCursor;
  }
  const plan = planOpenAll(search, openKeys, rules.org);
  const opened: Planned[] = [];
  for (const planned of plan.open) if (await openPr(planned, rules)) opened.push(planned);
  await updateMeta((meta) => seedOpen(meta, opened));
  await chrome.action.setBadgeText({ text: plan.remaining ? `${plan.estimated ? "~" : "+"}${plan.remaining}` : "" });
  await ensureAlarm();
  return { opened: opened.length, remaining: plan.remaining, estimated: plan.estimated };
}

// Focuses the PR's open tab (window, group, tab) or opens it in its architect group, one request at a time.
export const goTo = (input?: string, repo?: string) => serial(() => findOrOpen(input, repo));

// What the popup needs for the repo dropdown: the listed repos, the selected one, and whether to show it.
export async function repoChoices() {
  const rules = await loadRules();
  const { prTabsRepo } = await chrome.storage.local.get<{ prTabsRepo: string }>({ prTabsRepo: "" });
  return { repos: effectiveRepos(rules), selected: pickedRepo(rules, prTabsRepo), show: showRepoPicker(rules) };
}

// Remembers the dropdown pick when it is a listed repo.
export async function pickRepo(repo?: string): Promise<void> {
  const rules = await loadRules();
  if (repo && repo === pickedRepo(rules, repo)) await chrome.storage.local.set({ prTabsRepo: repo });
}

async function findOrOpen(input?: string, repo?: string) {
  const rules = await loadRules();
  if (!rules.org) return { error: "Set pr_tabs.org in orchestration.toml" };
  if (/^#?\d+$/.test((input || "").trim()) && !effectiveRepos(rules).length)
    return { error: "Set pr_tabs.default_repo in orchestration.toml" };
  const { prTabsRepo } = await chrome.storage.local.get<{ prTabsRepo: string }>({ prTabsRepo: "" });
  const pr = parseTarget(input, rules, repo || prTabsRepo);
  if (!pr) return { error: `Not a ${rules.org} PR number or URL` };
  const focused = await chrome.windows.getLastFocused().catch(() => null);
  const entry = findOpenTab(await prEntries(rules), pr.key, focused?.id);
  if (!entry) {
    const tab = await chrome.tabs.create({ url: pr.url, active: true });
    await groupTab(tab, pr, rules, await readMeta());
    await ensureAlarm();
    return { opened: pr.key };
  }
  const { tab } = entry;
  await chrome.windows.update(tab.windowId, { focused: true });
  if (tab.groupId !== chrome.tabGroups.TAB_GROUP_ID_NONE) {
    const group = await chrome.tabGroups.get(tab.groupId);
    if (group.collapsed) await chrome.tabGroups.update(group.id, { collapsed: false });
  }
  await chrome.tabs.update(tab.id!, { active: true });
  return { focused: pr.key };
}

const focusedId = async () => (await chrome.windows.getLastFocused().catch(() => null))?.id;

// Keeps one tab per PR (the active one, else the oldest) and closes the rest; each close is re-decided from the current tabs.
export const closeDuplicates = () =>
  serial(async () => {
    let closed = 0;
    const first = await openPrTabs();
    const keys = new Map(first.map((tab) => [tab.id, prKey(tab.url)]));
    for (const id of duplicateTabIds(first, await focusedId())) {
      const fresh = await openPrTabs();
      if (
        !duplicateTabIds(fresh, await focusedId()).includes(id) ||
        prKey(fresh.find((tab) => tab.id === id)!.url) !== keys.get(id)
      )
        continue;
      if (
        await chrome.tabs.remove(id).then(
          () => true,
          () => false,
        )
      )
        closed++;
    }
    return { closed };
  });

async function summary() {
  const entries = await prEntries(await loadRules());
  const ids = [
    ...new Set(entries.map(({ tab }) => tab.groupId).filter((id) => id !== chrome.tabGroups.TAB_GROUP_ID_NONE)),
  ];
  const groups = await Promise.all(ids.map((id) => chrome.tabGroups.get(id).catch(() => null)));
  return summarize(
    entries,
    new Map(
      groups
        .filter((group): group is chrome.tabGroups.TabGroup => Boolean(group))
        .map((group) => [group.id, group] as const),
    ),
  );
}

const pending = new Map<number, ReturnType<typeof setTimeout>>();
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (!tabUpdateMatters(change, tab, prTabIds.has(tabId))) return;
  clearTimeout(pending.get(tabId));
  pending.set(
    tabId,
    setTimeout(() => {
      pending.delete(tabId);
      void serial(() => groupTabId(tabId)).then(ensureAlarm);
    }, GROUP_DEBOUNCE_MS),
  );
});

chrome.tabs.onRemoved.addListener((tabId) => {
  clearTimeout(pending.get(tabId));
  pending.delete(tabId);
  ensureAlarm().catch((error: unknown) => console.warn("PR tabs:", error));
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) void poll();
});

chrome.runtime.onMessage.addListener(
  messageListener({
    openAll,
    goTo: (message) => goTo(message.input as string | undefined, message.repo as string | undefined),
    repos: repoChoices,
    pickRepo: (message) => pickRepo(message.repo as string | undefined),
    summary,
    checkNow,
    closeDuplicates,
  }),
);

// Groups the PR tabs already open and polls soon; restored tabs may still be arriving, so the alarm is armed regardless.
async function start(): Promise<void> {
  await chrome.alarms.create(ALARM, { delayInMinutes: FIRST_POLL_SECONDS / 60 });
  const rules = await loadRules();
  const meta = await readMeta();
  await serial(async () => {
    for (const { tab, pr } of await prEntries(rules)) {
      await groupTab(tab, pr, rules, meta).catch((error: unknown) => console.warn("PR tabs:", error));
    }
  });
}

chrome.runtime.onStartup.addListener(() => start().catch((error: unknown) => console.warn("PR tabs:", error)));
chrome.runtime.onInstalled.addListener(() => start().catch((error: unknown) => console.warn("PR tabs:", error)));
