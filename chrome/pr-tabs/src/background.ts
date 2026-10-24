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
  type CheckResult,
  type Closed,
  type Meta,
  type OpenAllResult,
  type Owners,
  type Planned,
  type Pr,
  type PrState,
  type Rules,
  type SearchHit,
  type States,
  type Summary,
} from "./core";
import {
  errorText,
  messageListener,
  type Failure,
  type RepoChoices,
  type Reply,
  type RequestOf,
} from "./messages";

type Tab = chrome.tabs.Tab;
type TabGroup = chrome.tabGroups.TabGroup;
type IdTab = Tab & { id: number };
type GroupInfo = ReturnType<typeof groupForPr>;
interface PrEntry {
  tab: IdTab;
  pr: Pr;
}

interface Ownership {
  owners?: Owners | null;
}
interface StatesData extends Ownership {
  prs: States;
}
interface SearchData extends Ownership {
  prs: SearchHit[];
  total: number;
  hasNextPage?: boolean;
  endCursor?: string;
}
interface HostData {
  settings: Partial<Rules>;
  states: StatesData;
  search: SearchData;
}
type PrRef = Pick<Pr, "owner" | "repo" | "number">;
type HostRequest =
  | { type: "settings" }
  | { type: "states"; prs: PrRef[] }
  | { type: "search"; query: string; limit: number; after?: string };
type HostOk<T> = { ok: true } & T;
type HostReply<T> = HostOk<T> | { ok: false; error?: string };

interface StoredState {
  prTabsSettings: HostOk<Partial<Rules>> | null;
  owners: Owners;
  meta: Meta;
  failures: number;
  cursor: number;
  prTabsRepo: string;
}
const STORED_DEFAULTS: StoredState = {
  prTabsSettings: null,
  owners: {},
  meta: {},
  failures: 0,
  cursor: 0,
  prTabsRepo: "",
};

const HOST = "orch.pr_tabs";
const ALARM = "pr-tabs-poll";
const HOST_TIMEOUT_MS = 45000;
const GROUP_DEBOUNCE_MS = 400;
const POLL_SOON_MS = 5000;
const SEARCH_LIMIT = 100;
const SETTINGS_RETRY_MS = 60000;
const FINISHED = new Set(["MERGED", "CLOSED"]);

async function readStored<K extends keyof StoredState>(key: K): Promise<StoredState[K]> {
  const stored = await chrome.storage.local.get<Partial<StoredState>>(key);
  return stored[key] ?? STORED_DEFAULTS[key];
}

function writeStored(values: Partial<StoredState>): Promise<void> {
  return chrome.storage.local.set(values);
}

function hasId(tab: Tab): tab is IdTab {
  return tab.id !== undefined;
}

// Chrome gives every tab it creates an id.
function withId(tab: Tab): IdTab {
  if (!hasId(tab)) {
    throw new Error("Chrome created a tab without an id");
  }
  return tab;
}

let owners: Owners | undefined;
let rulesPromise: Promise<Rules> | undefined;
let retryRulesAt = 0;

function expireRules(): void {
  if (retryRulesAt && Date.now() >= retryRulesAt) {
    rulesPromise = undefined;
    retryRulesAt = 0;
  }
}

function handleRulesFailed(error: unknown): never {
  retryRulesAt ||= Date.now() + SETTINGS_RETRY_MS;
  throw error;
}

function loadRules(): Promise<Rules> {
  expireRules();
  rulesPromise ||= fetchRules().catch(handleRulesFailed);
  return rulesPromise;
}

async function fetchRules(): Promise<Rules> {
  const response = await fetch(chrome.runtime.getURL("rules.json"));
  // rules.json ships with the extension.
  const rules = (await response.json()) as Rules;
  return configure(rules);
}

// The packaged rules with the host's settings on top; the cached settings when the host fails.
async function configure(rules: Rules): Promise<Rules> {
  try {
    const settings = await askHost({ type: "settings" });
    await writeStored({ prTabsSettings: settings });
    return { ...rules, ...settings };
  } catch (error) {
    return cachedRules(rules, error);
  }
}

async function cachedRules(rules: Rules, error: unknown): Promise<Rules> {
  retryRulesAt = Date.now() + SETTINGS_RETRY_MS;
  const settings = await readStored("prTabsSettings");
  if (!settings) {
    await chrome.alarms.create(ALARM, { delayInMinutes: SETTINGS_RETRY_MS / 60000 });
    throw error;
  }
  console.warn("PR tabs: using cached settings:", error);
  return { ...rules, ...settings };
}

// One native host process per request; the host exits after replying.
function askHost<M extends HostRequest>(message: M): Promise<HostOk<HostData[M["type"]]>> {
  return new Promise((resolve, reject) => {
    function handleHostTimeout() {
      reject(new Error("native host timed out"));
    }
    const timer = setTimeout(handleHostTimeout, HOST_TIMEOUT_MS);
    function handleHostReply(reply?: HostReply<HostData[M["type"]]>) {
      clearTimeout(timer);
      const lastError = chrome.runtime.lastError;
      if (lastError) {
        reject(new Error(lastError.message));
        return;
      }
      if (!reply?.ok) {
        reject(new Error(reply?.error || "empty reply from native host"));
        return;
      }
      resolve(reply);
    }
    chrome.runtime.sendNativeMessage(HOST, message, handleHostReply);
  });
}

function warn(error: unknown): void {
  console.warn("PR tabs:", error);
}

let chain: Promise<unknown> = Promise.resolve();
function serial<T>(task: () => Promise<T>): Promise<T> {
  const run = chain.then(task, task);
  chain = run.catch(warn);
  return run;
}

// Tab IDs that showed a PR at the last scan.
const prTabIds = new Set<number>();

function isOpenPrTab(tab: Tab): tab is IdTab {
  return hasId(tab) && !tab.incognito;
}

// Normal-window github.com PR tabs; incognito tabs are left out.
async function openPrTabs(): Promise<IdTab[]> {
  const tabs = await chrome.tabs.query({
    url: "https://github.com/*/*/pull/*",
    windowType: "normal",
  });
  return tabs.filter(isOpenPrTab);
}

function entryOf(tab: IdTab, org: string): PrEntry[] {
  const pr = parsePrUrl(tab.url, org);
  return pr ? [{ tab, pr }] : [];
}

export async function prEntries(rules: Pick<Rules, "org">): Promise<PrEntry[]> {
  const tabs = await openPrTabs();
  const entries = tabs.flatMap((tab) => entryOf(tab, rules.org));
  prTabIds.clear();
  for (const { tab } of entries) {
    prTabIds.add(tab.id);
  }
  return entries;
}

async function findTab(id: number): Promise<IdTab | null> {
  try {
    const tab = await chrome.tabs.get(id);
    return tab && hasId(tab) ? tab : null;
  } catch {
    return null;
  }
}

// The tab with its PR, or null when it is gone or shows no PR of org.
async function prTab(id: number, org: string): Promise<PrEntry | null> {
  const tab = await findTab(id);
  const [entry] = tab ? entryOf(tab, org) : [];
  return entry ?? null;
}

async function removeTab(id: number): Promise<boolean> {
  try {
    await chrome.tabs.remove(id);
    return true;
  } catch {
    return false;
  }
}

async function focusedId(): Promise<number | undefined> {
  try {
    return (await chrome.windows.getLastFocused()).id;
  } catch {
    return undefined;
  }
}

function isOwners(next: Owners | null | undefined): next is Owners {
  return Boolean(next) && typeof next === "object" && !Array.isArray(next);
}

// null/absent ownership means discovery failed; an empty object is a good scan.
async function recordOwners(next: Owners | null | undefined): Promise<void> {
  if (!isOwners(next)) {
    return;
  }
  owners = next;
  await writeStored({ owners });
}

function readMeta(): Promise<Meta> {
  return readStored("meta");
}

let metaChain: Promise<unknown> = Promise.resolve();
// Every metadata write runs here, one at a time: read, change, write.
function updateMeta(change: (meta: Meta) => Meta | Promise<Meta>): Promise<void> {
  async function rewrite() {
    const meta = await readMeta();
    await writeStored({ meta: await change(meta) });
  }
  const run = metaChain.then(rewrite);
  metaChain = run.catch(warn);
  return run;
}

async function groupFor(windowId: number, name: string): Promise<TabGroup | null> {
  const [group] = await chrome.tabGroups.query({ windowId, title: name });
  return group ?? null;
}

async function knownOwners(): Promise<Owners> {
  if (owners === undefined) {
    const saved = await readStored("owners");
    // A host refresh can finish while storage is being read.
    owners ??= saved;
    return owners;
  }
  return owners;
}

async function createGroup(tab: IdTab, info: GroupInfo): Promise<void> {
  const groupId = await chrome.tabs.group({
    tabIds: [tab.id],
    createProperties: { windowId: tab.windowId },
  });
  await chrome.tabGroups.update(groupId, { title: info.name, color: info.color });
}

async function moveIntoGroup(tab: IdTab, info: GroupInfo): Promise<void> {
  const group = await groupFor(tab.windowId, info.name);
  if (!group) {
    await createGroup(tab, info);
    return;
  }
  if (group.color !== info.color) {
    await chrome.tabGroups.update(group.id, { color: info.color });
  }
  if (tab.groupId !== group.id) {
    await chrome.tabs.group({ groupId: group.id, tabIds: [tab.id] });
  }
}

// Moves the tab into its architect group; a tab already there is left alone.
async function groupTab(tab: IdTab, pr: Pr, meta: Meta): Promise<void> {
  if (tab.pinned) {
    return;
  }
  const known = await knownOwners();
  const info: PrState = meta[pr.key] ?? {};
  const head = { branch: info.headRefName, headRepository: info.headRepository };
  await moveIntoGroup(tab, groupForPr(known, { ...pr, ...head }));
}

async function groupTabId(tabId: number): Promise<void> {
  const rules = await loadRules();
  const entry = await prTab(tabId, rules.org);
  if (!entry) {
    return;
  }
  const meta = await readMeta();
  await groupTab(entry.tab, entry.pr, meta);
  if (!meta[entry.pr.key]) {
    pollSoon();
  }
}

// Drops metadata of PRs without a tab.
function pruneClosedTabs(rules: Rules): Promise<void> {
  async function pruneWithoutTab(meta: Meta) {
    const keys = new Set((await prEntries(rules)).map(({ pr }) => pr.key));
    return pruneMeta(meta, keys);
  }
  return updateMeta(pruneWithoutTab);
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

function handlePollSoonTimer(): void {
  void poll();
}

let pollTimer: ReturnType<typeof setTimeout> | undefined;
function pollSoon(): void {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(handlePollSoonTimer, POLL_SOON_MS);
}

type CheckReply = Reply<"checkNow">;
let current: Promise<CheckReply> | null = null;
let pollAgain = false;

function handleCheckSettled(): void {
  current = null;
  if (pollAgain) {
    pollAgain = false;
    void poll();
    return;
  }
  ensureAlarm().catch(warn);
}

// One check at a time; a check requested during one runs once after it.
function poll(): Promise<CheckReply> {
  if (current) {
    pollAgain = true;
    return current;
  }
  current = checkOnce().finally(handleCheckSettled);
  return current;
}

export async function checkNow(): Promise<CheckReply> {
  if (current) {
    await current;
  }
  return poll();
}

interface Fetched {
  states: States;
  prs: number;
  checked: number;
  nextCursor: number;
}

function prRefs(batch: string[], byKey: Map<string, Pr>): PrRef[] {
  return batch.flatMap((key) => {
    const pr = byKey.get(key);
    return pr ? [{ owner: pr.owner, repo: pr.repo, number: pr.number }] : [];
  });
}

async function pollFailed(failures: number, error: unknown): Promise<Failure> {
  const message = errorText(error);
  console.warn("PR tabs poll failed:", message);
  await writeStored({ failures: failures + 1 });
  await chrome.alarms.create(ALARM, { delayInMinutes: nextDelaySeconds(failures + 1) / 60 });
  return { error: `GitHub check failed: ${message}` };
}

// The states of the next batch of PRs in one host call.
async function fetchStates(entries: PrEntry[]): Promise<Fetched | Failure> {
  const byKey = new Map(entries.map(({ pr }) => [pr.key, pr]));
  const failures = await readStored("failures");
  const cursor = await readStored("cursor");
  const { batch, nextCursor } = pollBatch([...byKey.keys()].toSorted(), cursor);
  try {
    const reply = await askHost({ type: "states", prs: prRefs(batch, byKey) });
    await recordOwners(reply.owners);
    return { states: reply.prs, prs: byKey.size, checked: batch.length, nextCursor };
  } catch (error) {
    return pollFailed(failures, error);
  }
}

async function closeIfFinished(id: number, key: string, org: string): Promise<boolean> {
  const tab = await findTab(id);
  if (!tab || tab.active || !stillShowsPr(tab, key, org)) {
    return false;
  }
  return removeTab(id);
}

// Closes background tabs of merged or closed PRs that still show their PR.
async function closeFinished(rules: Rules, entries: PrEntry[], states: States): Promise<Closed[]> {
  const keyById = new Map(entries.map(({ tab, pr }) => [tab.id, pr.key]));
  const closed: Closed[] = [];
  for (const id of tabsToClose(entries, states)) {
    const key = keyById.get(id);
    if (key !== undefined && (await closeIfFinished(id, key, rules.org))) {
      closed.push({ key, state: states[key]?.state });
    }
  }
  return closed;
}

function activeFinished(entries: PrEntry[], states: States): string[] {
  return entries
    .filter(({ tab, pr }) => tab.active && FINISHED.has(states[pr.key]?.state ?? ""))
    .map(({ pr }) => pr.key);
}

async function groupEntries(rules: Rules, entries: PrEntry[], meta: Meta): Promise<void> {
  for (const { tab } of entries) {
    const entry = await prTab(tab.id, rules.org);
    if (entry) {
      await groupTab(entry.tab, entry.pr, meta).catch(warn);
    }
  }
}

async function regroup(rules: Rules, entries: PrEntry[]): Promise<void> {
  const meta = await readMeta();
  await serial(() => groupEntries(rules, entries, meta));
}

async function applyStates(rules: Rules, entries: PrEntry[], fetched: Fetched) {
  const { states, prs, checked, nextCursor } = fetched;
  const closed = await closeFinished(rules, entries, states);
  const inView = activeFinished(entries, states);
  await updateMeta((meta) => recordStates(meta, states));
  await writeStored({ failures: 0, cursor: nextCursor });
  await chrome.alarms.create(ALARM, { delayInMinutes: nextDelaySeconds(0) / 60 });
  await regroup(rules, entries);
  return { tabs: entries.length, prs, checked, closed, inView };
}

function noTabsChecked(): CheckResult {
  return { tabs: 0, prs: 0, checked: 0, closed: [], inView: [] };
}

// Fetches the PR states in one host call, closes background tabs of merged or closed PRs and reports what happened.
async function checkOnce(): Promise<CheckReply> {
  const rules = await loadRules();
  const entries = await prEntries(rules);
  if (!entries.length) {
    await chrome.alarms.clear(ALARM);
    return noTabsChecked();
  }
  const fetched = await fetchStates(entries);
  if ("error" in fetched) {
    return fetched;
  }
  return applyStates(rules, entries, fetched);
}

// Re-checks the open tabs inside the serial chain, so a tab opened since the search is not duplicated.
function openPr({ hit, pr }: Planned, rules: Rules): Promise<boolean> {
  async function openIfMissing() {
    if ((await prEntries(rules)).some((entry) => entry.pr.key === pr.key)) {
      return false;
    }
    const tab = withId(await chrome.tabs.create({ url: pr.url, active: false }));
    const { title, headRefName, headRepository } = hit;
    await groupTab(tab, pr, { [pr.key]: { title, headRefName, headRepository } });
    return true;
  }
  return serial(openIfMissing);
}

function searchPage(query: string, after: string | undefined) {
  return askHost({
    type: "search",
    query,
    limit: SEARCH_LIMIT,
    ...(after && { after }),
  });
}

interface Search {
  total: number;
  prs: SearchHit[];
}

function hasEnough(search: Search, openKeys: Set<string>, rules: Rules): boolean {
  return planOpenAll(search, openKeys, rules.org).open.length >= OPEN_ALL_CAP;
}

// Search pages until there are none left or enough PRs to open.
async function searchOpenPrs(rules: Rules, openKeys: Set<string>): Promise<Search> {
  const search: Search = { total: 0, prs: [] };
  let after: string | undefined;
  for (let page = 0; page < MAX_SEARCH_PAGES; page++) {
    const reply = await searchPage(rules.openAll, after);
    await recordOwners(reply.owners);
    search.total = reply.total;
    search.prs.push(...reply.prs);
    if (!reply.hasNextPage || hasEnough(search, openKeys, rules)) {
      break;
    }
    after = reply.endCursor;
  }
  return search;
}

async function openPlanned(plan: Planned[], rules: Rules): Promise<Planned[]> {
  const opened: Planned[] = [];
  for (const planned of plan) {
    if (await openPr(planned, rules)) {
      opened.push(planned);
    }
  }
  return opened;
}

function badgeText({ remaining, estimated }: Pick<OpenAllResult, "remaining" | "estimated">) {
  if (!remaining) {
    return "";
  }
  return `${estimated ? "~" : "+"}${remaining}`;
}

export async function openAll(): Promise<Reply<"openAll">> {
  const rules = await loadRules();
  if (!rules.org || !rules.openAll) {
    return { error: "Set pr_tabs.org and pr_tabs.open_all in orchestration.toml" };
  }
  const openKeys = new Set((await prEntries(rules)).map(({ pr }) => pr.key));
  const plan = planOpenAll(await searchOpenPrs(rules, openKeys), openKeys, rules.org);
  const opened = await openPlanned(plan.open, rules);
  await updateMeta((meta) => seedOpen(meta, opened));
  await chrome.action.setBadgeText({ text: badgeText(plan) });
  await ensureAlarm();
  return { opened: opened.length, remaining: plan.remaining, estimated: plan.estimated };
}

// Focuses the PR's open tab (window, group, tab) or opens it in its architect group, one request at a time.
export function goTo(input?: string, repo?: string): Promise<Reply<"goTo">> {
  return serial(() => findOrOpen(input, repo));
}

// What the popup needs for the repo dropdown: the listed repos, the selected one, and whether to show it.
export async function repoChoices(): Promise<RepoChoices> {
  const rules = await loadRules();
  const stored = await readStored("prTabsRepo");
  return {
    repos: effectiveRepos(rules),
    selected: pickedRepo(rules, stored),
    show: showRepoPicker(rules),
  };
}

// Remembers the dropdown pick when it is a listed repo.
export async function pickRepo(repo?: string): Promise<void> {
  const rules = await loadRules();
  if (repo && repo === pickedRepo(rules, repo)) {
    await writeStored({ prTabsRepo: repo });
  }
}

function configProblem(rules: Rules, input: string | undefined): string | null {
  if (!rules.org) {
    return "Set pr_tabs.org in orchestration.toml";
  }
  if (/^#?\d+$/.test((input || "").trim()) && !effectiveRepos(rules).length) {
    return "Set pr_tabs.default_repo in orchestration.toml";
  }
  return null;
}

async function openTarget(pr: Pr): Promise<Reply<"goTo">> {
  const tab = withId(await chrome.tabs.create({ url: pr.url, active: true }));
  await groupTab(tab, pr, await readMeta());
  await ensureAlarm();
  return { opened: pr.key };
}

async function expandGroup(groupId: number): Promise<void> {
  if (groupId === chrome.tabGroups.TAB_GROUP_ID_NONE) {
    return;
  }
  const group = await chrome.tabGroups.get(groupId);
  if (group.collapsed) {
    await chrome.tabGroups.update(group.id, { collapsed: false });
  }
}

async function focusTab(tab: IdTab): Promise<void> {
  await chrome.windows.update(tab.windowId, { focused: true });
  await expandGroup(tab.groupId);
  await chrome.tabs.update(tab.id, { active: true });
}

async function findOrOpen(input?: string, repo?: string): Promise<Reply<"goTo">> {
  const rules = await loadRules();
  const problem = configProblem(rules, input);
  if (problem) {
    return { error: problem };
  }
  const stored = await readStored("prTabsRepo");
  const pr = parseTarget(input, rules, repo || stored);
  if (!pr) {
    return { error: `Not a ${rules.org} PR number or URL` };
  }
  const focused = await focusedId();
  const entry = findOpenTab(await prEntries(rules), pr.key, focused);
  if (!entry) {
    return openTarget(pr);
  }
  await focusTab(entry.tab);
  return { focused: pr.key };
}

// Closes id when it is still a duplicate of the PR it showed at the first scan.
async function closeDuplicate(id: number, key: string | null | undefined): Promise<boolean> {
  const fresh = await openPrTabs();
  const tab = fresh.find((candidate) => candidate.id === id);
  if (!tab || prKey(tab.url) !== key) {
    return false;
  }
  if (!duplicateTabIds(fresh, await focusedId()).includes(id)) {
    return false;
  }
  return removeTab(id);
}

async function closeEachDuplicate(): Promise<{ closed: number }> {
  let closed = 0;
  const first = await openPrTabs();
  const keys = new Map(first.map((tab) => [tab.id, prKey(tab.url)]));
  for (const id of duplicateTabIds(first, await focusedId())) {
    if (await closeDuplicate(id, keys.get(id))) {
      closed++;
    }
  }
  return { closed };
}

// Keeps one tab per PR (the active one, else the oldest) and closes the rest; each close is re-decided from the current tabs.
export function closeDuplicates(): Promise<{ closed: number }> {
  return serial(closeEachDuplicate);
}

function isGroup(group: TabGroup | null): group is TabGroup {
  return group !== null;
}

async function findGroup(id: number): Promise<TabGroup | null> {
  try {
    return await chrome.tabGroups.get(id);
  } catch {
    return null;
  }
}

async function groupsById(entries: PrEntry[]): Promise<Map<number, TabGroup>> {
  const none = chrome.tabGroups.TAB_GROUP_ID_NONE;
  const ids = [...new Set(entries.map(({ tab }) => tab.groupId))].filter((id) => id !== none);
  const groups = await Promise.all(ids.map(findGroup));
  return new Map(groups.filter(isGroup).map((group) => [group.id, group]));
}

async function summary(): Promise<Summary> {
  const entries = await prEntries(await loadRules());
  return summarize(entries, await groupsById(entries));
}

const pending = new Map<number, ReturnType<typeof setTimeout>>();

function handleTabSettled(tabId: number): void {
  pending.delete(tabId);
  void serial(() => groupTabId(tabId)).then(ensureAlarm);
}

function handleTabUpdated(tabId: number, change: chrome.tabs.OnUpdatedInfo, tab: Tab): void {
  if (!tabUpdateMatters(change, tab, prTabIds.has(tabId))) {
    return;
  }
  clearTimeout(pending.get(tabId));
  pending.set(tabId, setTimeout(handleTabSettled, GROUP_DEBOUNCE_MS, tabId));
}

function handleTabRemoved(tabId: number): void {
  clearTimeout(pending.get(tabId));
  pending.delete(tabId);
  ensureAlarm().catch(warn);
}

function handleAlarmFired(alarm: chrome.alarms.Alarm): void {
  if (alarm.name === ALARM) {
    void poll();
  }
}

function handleOpenAllMessage() {
  return openAll();
}

function handleGoToMessage(message: RequestOf<"goTo">) {
  return goTo(message.input, message.repo);
}

function handleReposMessage() {
  return repoChoices();
}

function handlePickRepoMessage(message: RequestOf<"pickRepo">) {
  return pickRepo(message.repo);
}

function handleSummaryMessage() {
  return summary();
}

function handleCheckNowMessage() {
  return checkNow();
}

function handleCloseDuplicatesMessage() {
  return closeDuplicates();
}

const handleRuntimeMessage = messageListener({
  openAll: handleOpenAllMessage,
  goTo: handleGoToMessage,
  repos: handleReposMessage,
  pickRepo: handlePickRepoMessage,
  summary: handleSummaryMessage,
  checkNow: handleCheckNowMessage,
  closeDuplicates: handleCloseDuplicatesMessage,
});

async function groupOpenTabs(rules: Rules, meta: Meta): Promise<void> {
  for (const { tab, pr } of await prEntries(rules)) {
    await groupTab(tab, pr, meta).catch(warn);
  }
}

// Groups the PR tabs already open and polls soon; restored tabs may still be arriving, so the alarm is armed regardless.
async function start(): Promise<void> {
  await chrome.alarms.create(ALARM, { delayInMinutes: FIRST_POLL_SECONDS / 60 });
  const rules = await loadRules();
  const meta = await readMeta();
  await serial(() => groupOpenTabs(rules, meta));
}

function handleBrowserStartup(): Promise<void> {
  return start().catch(warn);
}

function handleExtensionInstalled(): Promise<void> {
  return start().catch(warn);
}

chrome.tabs.onUpdated.addListener(handleTabUpdated);
chrome.tabs.onRemoved.addListener(handleTabRemoved);
chrome.alarms.onAlarm.addListener(handleAlarmFired);
chrome.runtime.onMessage.addListener(handleRuntimeMessage);
chrome.runtime.onStartup.addListener(handleBrowserStartup);
chrome.runtime.onInstalled.addListener(handleExtensionInstalled);
