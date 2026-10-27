import {
  duplicateTabIds,
  prKey,
  findOpenTab,
  tabUpdateMatters,
  nextDelaySeconds,
  isBareNumber,
  parsePrUrl,
  parseTarget,
  splitTargets,
  planOpenAll,
  pollBatch,
  pruneMeta,
  recordStates,
  seedOpen,
  stillShowsPr,
  summarize,
  MAX_SEARCH_PAGES,
  OPEN_ALL_CAP,
  MAX_TARGETS,
  FIRST_POLL_SECONDS,
  tabsToClose,
  groupForPr,
  effectiveRepos,
  pickedRepo,
  showRepoPicker,
  MAX_PANE_LOOKUPS,
  MAX_PRS_PER_POLL,
} from "#/core";
import { getMessage } from "#/i18n";
import { errorText, messageListener } from "#/messages";
import type {
  BranchRef,
  CheckResult,
  Closed,
  DuplicatesClosed,
  Failure,
  FetchedStates,
  FocusedPane,
  GroupId,
  GroupPlacement,
  GroupTitle,
  HostReplies,
  HostOk,
  HostReply,
  HostRequest,
  IdTab,
  Meta,
  MetadataRevision,
  OpenAllPlan,
  OpenBatchAction,
  OpeningProgress,
  OrgName,
  Owners,
  PaneLookup,
  Planned,
  Pr,
  PrEntry,
  PrKey,
  PrRef,
  PrState,
  PrStateName,
  RepoChoices,
  RepoName,
  Reply,
  RequestOf,
  Rules,
  SearchReply,
  SearchResult,
  States,
  StoredState,
  Summary,
  Tab,
  TabGroup,
  TabId,
  WindowId,
} from "#/types";

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
const FINISHED: ReadonlySet<PrStateName> = new Set(["MERGED", "CLOSED"]);

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
    throw new Error(getMessage("errTabWithoutId"));
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
function askHost<M extends HostRequest>(message: M): Promise<HostOk<HostReplies[M["type"]]>> {
  return new Promise((resolve, reject) => {
    function handleHostTimeout(): void {
      reject(new Error(getMessage("errHostTimeout")));
    }

    const timer = setTimeout(handleHostTimeout, HOST_TIMEOUT_MS);

    function handleHostReply(reply?: HostReply<HostReplies[M["type"]]>): void {
      clearTimeout(timer);

      const lastError = chrome.runtime.lastError;

      if (lastError) {
        reject(new Error(lastError.message));

        return;
      }

      if (!reply?.ok) {
        reject(new Error(reply?.error || getMessage("errHostEmptyReply")));

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
const prTabIds = new Set<TabId>();

function isOpenPrTab(tab: Tab): tab is IdTab {
  return hasId(tab) && !tab.incognito && prKey(tab.pendingUrl || tab.url) !== null;
}

// Normal-window github.com PR tabs; incognito tabs are left out.
async function openPrTabs(): Promise<IdTab[]> {
  const tabs = await chrome.tabs.query({
    windowType: "normal",
  });

  return tabs
    .filter(isOpenPrTab)
    .map((tab) => Object.assign({}, tab, { url: tab.pendingUrl || tab.url }));
}

function entryOf(tab: IdTab, org: OrgName): PrEntry[] {
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

async function findTab(id: TabId): Promise<IdTab | null> {
  try {
    const tab = await chrome.tabs.get(id);

    return tab && hasId(tab) ? tab : null;
  } catch {
    return null;
  }
}

async function prTab(id: TabId, org: OrgName): Promise<PrEntry | null> {
  const tab = await findTab(id);
  const [entry] = tab ? entryOf(tab, org) : [];

  return entry ?? null;
}

async function removeTab(id: TabId): Promise<boolean> {
  try {
    await chrome.tabs.remove(id);

    return true;
  } catch {
    return false;
  }
}

async function focusedId(): Promise<WindowId | undefined> {
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
  async function rewrite(): Promise<void> {
    const meta = await readMeta();
    await writeStored({ meta: await change(meta) });
  }

  const run = metaChain.then(rewrite);

  metaChain = run.catch(warn);

  return run;
}

async function groupFor(windowId: WindowId, name: GroupTitle): Promise<TabGroup | null> {
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

async function createGroup(tab: IdTab, placement: GroupPlacement): Promise<void> {
  const groupId = await chrome.tabs.group({
    tabIds: [tab.id],
    createProperties: { windowId: tab.windowId },
  });

  await chrome.tabGroups.update(groupId, { title: placement.name, color: placement.color });
}

async function moveIntoGroup(tab: IdTab, placement: GroupPlacement): Promise<void> {
  const group = await groupFor(tab.windowId, placement.name);

  if (!group) {
    await createGroup(tab, placement);

    return;
  }

  if (group.color !== placement.color) {
    await chrome.tabGroups.update(group.id, { color: placement.color });
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
  const recorded: PrState = meta[pr.key] ?? {};
  const head = { branch: recorded.headRefName, headRepository: recorded.headRepository };

  await moveIntoGroup(tab, groupForPr(known, { ...pr, ...head }));
}

async function groupTabId(tabId: TabId): Promise<void> {
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
  async function pruneWithoutTab(meta: Meta): Promise<Meta> {
    const keys = new Set((await prEntries(rules)).map((entry) => entry.pr.key));

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

let current: Promise<Reply<"checkNow">> | null = null;
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
function poll(): Promise<Reply<"checkNow">> {
  if (current) {
    pollAgain = true;

    return current;
  }

  current = checkOnce().finally(handleCheckSettled);

  return current;
}

export async function checkNow(): Promise<Reply<"checkNow">> {
  if (current) {
    await current;
  }

  return poll();
}

function prRefs(batch: PrKey[], byKey: Map<PrKey, Pr>): PrRef[] {
  return batch.flatMap((key) => {
    const pr = byKey.get(key);

    return pr ? [{ owner: pr.owner, repo: pr.repo, number: pr.number }] : [];
  });
}

async function pollFailed(failures: number, error: unknown): Promise<Failure> {
  const detail = errorText(error);

  console.warn("PR tabs poll failed:", detail);
  await writeStored({ failures: failures + 1 });
  await chrome.alarms.create(ALARM, { delayInMinutes: nextDelaySeconds(failures + 1) / 60 });

  return { error: getMessage("errGithubCheckFailed", { message: detail }) };
}

async function fetchStates(entries: PrEntry[]): Promise<FetchedStates | Failure> {
  const byKey = new Map(entries.map((entry) => [entry.pr.key, entry.pr]));
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

async function closeIfFinished(id: TabId, key: PrKey, org: OrgName): Promise<boolean> {
  const tab = await findTab(id);

  if (!tab || tab.active || !stillShowsPr(tab, key, org)) {
    return false;
  }

  return removeTab(id);
}

async function closeFinished(rules: Rules, entries: PrEntry[], states: States): Promise<Closed[]> {
  const keyById = new Map(entries.map((entry) => [entry.tab.id, entry.pr.key]));
  const closed: Closed[] = [];

  for (const id of tabsToClose(entries, states)) {
    const key = keyById.get(id);

    if (key !== undefined && (await closeIfFinished(id, key, rules.org))) {
      closed.push({ key, state: states[key]?.state });
    }
  }

  return closed;
}

function isFinished(entry: PrEntry, states: States): boolean {
  const state = states[entry.pr.key]?.state;

  return entry.tab.active && state !== undefined && FINISHED.has(state);
}

function activeFinished(entries: PrEntry[], states: States): PrKey[] {
  return entries.filter((entry) => isFinished(entry, states)).map((entry) => entry.pr.key);
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

async function applyStates(
  rules: Rules,
  entries: PrEntry[],
  fetched: FetchedStates,
): Promise<CheckResult> {
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

// Fetches the PR states in one host call, closes background tabs of merged or closed PRs and
// reports what happened.
async function checkOnce(): Promise<Reply<"checkNow">> {
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

// Re-checks the open tabs inside the serial chain, so a tab opened since the search is not
// duplicated.
function openPr(planned: Planned, rules: Rules): Promise<boolean> {
  const { hit, pr } = planned;

  async function openIfMissing(): Promise<boolean> {
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

function searchPage(query: string, after: string | undefined): Promise<HostOk<SearchReply>> {
  return askHost({
    type: "search",
    query,
    limit: SEARCH_LIMIT,
    ...(after && { after }),
  });
}

function hasEnough(search: SearchResult, openKeys: Set<PrKey>, rules: Rules): boolean {
  return planOpenAll(search, openKeys, rules.org).open.length >= OPEN_ALL_CAP;
}

async function searchOpenPrs(rules: Rules, openKeys: Set<PrKey>): Promise<SearchResult> {
  const search: SearchResult = { total: 0, prs: [] };
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
  progress = { active: true, opened: 0, planned: plan.length };

  for (const planned of plan) {
    if (await openPr(planned, rules)) {
      await updateMeta((meta) => seedOpen(meta, [planned])).catch(warn);
      opened.push(planned);
    }

    progress = { ...progress, opened: progress.opened + 1 };
  }

  return opened;
}

function badgeText(plan: OpenAllPlan): string {
  if (!plan.remaining) {
    return "";
  }

  return `${plan.estimated ? "~" : "+"}${plan.remaining}`;
}

async function openBatchOnce(action: OpenBatchAction): Promise<Reply<"openAll">> {
  const configured = await loadRules();
  const rules =
    action === "openReviews"
      ? {
          ...configured,
          openAll: ["is:pr", "is:open", `org:${configured.org}`, "review-requested:@me"].join(" "),
        }
      : configured;

  if (!rules.org || !rules.openAll) {
    return { error: getMessage(action === "openReviews" ? "errSetOrg" : "errSetOrgAndOpenAll") };
  }

  const openKeys = new Set((await prEntries(rules)).map((entry) => entry.pr.key));
  const plan = planOpenAll(await searchOpenPrs(rules, openKeys), openKeys, rules.org);
  const opened = await openPlanned(plan.open, rules);

  if (action === "openAll") {
    await chrome.action.setBadgeText({ text: badgeText(plan) });
  }

  await ensureAlarm();

  return { opened: opened.length, remaining: plan.remaining, estimated: plan.estimated };
}

let openingBatch: Promise<Reply<"openAll">> | undefined;
let progress: OpeningProgress = { active: false, opened: 0, planned: 0 };

export function openingProgress(): OpeningProgress {
  if (metadataFlight) {
    return { ...progress, hydrating: true, metadataRevision };
  }

  return metadataRevision > 0 ? { ...progress, metadataRevision } : { ...progress };
}

function handleOpenBatchSettled(): void {
  openingBatch = undefined;
  progress = { active: false, opened: 0, planned: 0, result: progress.result };
}

function recordOpeningResult(result: Reply<"openAll">): Reply<"openAll"> {
  progress = { ...progress, result };

  return result;
}

function handleOpeningFailed(error: unknown): never {
  recordOpeningResult({ error: errorText(error) });
  throw error;
}

function openBatch(action: OpenBatchAction): Promise<Reply<"openAll">> {
  if (!openingBatch) {
    progress = { active: true, opened: 0, planned: 0 };
    openingBatch = openBatchOnce(action)
      .then(recordOpeningResult, handleOpeningFailed)
      .finally(handleOpenBatchSettled);
  }

  return openingBatch;
}

export function openAll(): Promise<Reply<"openAll">> {
  return openBatch("openAll");
}

export function openReviews(): Promise<Reply<"openReviews">> {
  return openBatch("openReviews");
}

// Focuses the PR's open tab (window, group, tab) or opens it in its architect group, one request at
// a time. A pasted list does this for each item in order.
export function goTo(input?: string, repo?: RepoName): Promise<Reply<"goTo">> {
  return serial(() => findOrOpenEach(input, repo));
}

async function focusKnownPr(key: PrKey): Promise<Reply<"focusPr">> {
  const rules = await loadRules();
  const entry = findOpenTab(await prEntries(rules), key, await focusedId());

  if (!entry) {
    return { error: getMessage("errPrTabGone") };
  }

  await focusTab(entry.tab);

  return { focused: key };
}

export function focusPr(key: PrKey): Promise<Reply<"focusPr">> {
  return serial(() => focusKnownPr(key));
}

async function closeKnownPr(key: PrKey, tabId: TabId): Promise<Reply<"closePr">> {
  const rules = await loadRules();
  const tab = await findTab(tabId);

  if (!tab || parsePrUrl(tab.pendingUrl || tab.url, rules.org)?.key !== key) {
    return { closed: 0 };
  }

  return { closed: (await removeTab(tab.id)) ? 1 : 0 };
}

export function closePr(key: PrKey, tabId: TabId): Promise<Reply<"closePr">> {
  return serial(() => closeKnownPr(key, tabId));
}

// What the popup needs for the repo dropdown: the listed repos, the selected one, and whether to
// show it.
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
export async function pickRepo(repo?: RepoName): Promise<void> {
  const rules = await loadRules();

  if (repo && repo === pickedRepo(rules, repo)) {
    await writeStored({ prTabsRepo: repo });
  }
}

function configProblem(rules: Rules, input: string | undefined): string | null {
  if (!rules.org) {
    return getMessage("errSetOrg");
  }

  if (/^#?\d+$/.test((input || "").trim()) && !effectiveRepos(rules).length) {
    return getMessage("errSetDefaultRepo");
  }

  return null;
}

async function openTarget(pr: Pr): Promise<Reply<"goTo">> {
  const tab = withId(await chrome.tabs.create({ url: pr.url, active: true }));

  await groupTab(tab, pr, await readMeta());
  await ensureAlarm();

  return { opened: pr.key };
}

async function expandGroup(groupId: GroupId): Promise<void> {
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

async function findOrOpen(input?: string, repo?: RepoName): Promise<Reply<"goTo">> {
  const rules = await loadRules();
  const problem = configProblem(rules, input);

  if (problem) {
    return { error: problem };
  }

  const stored = await readStored("prTabsRepo");
  const pr = parseTarget(input, rules, repo || stored);

  if (!pr) {
    return { error: getMessage("errNotAPr", { org: rules.org }) };
  }

  const focused = await focusedId();
  const entry = findOpenTab(await prEntries(rules), pr.key, focused);

  if (!entry) {
    return openTarget(pr);
  }

  await focusTab(entry.tab);

  return { focused: pr.key };
}

async function openEach(items: string[], repo?: RepoName): Promise<Reply<"goTo">> {
  const failed: string[] = [];
  let last: Reply<"goTo"> | undefined;

  for (const item of items) {
    const reply = await findOrOpen(item, repo);

    if ("error" in reply) {
      failed.push(item);
    } else {
      last = reply;
    }
  }

  if (last && !failed.length) {
    return last;
  }

  return {
    error: getMessage("errCouldntOpen", { items: (failed.length ? failed : items).join(", ") }),
  };
}

async function findOrOpenEach(input?: string, repo?: RepoName): Promise<Reply<"goTo">> {
  const items = splitTargets(input);

  if (items.length < 2) {
    return findOrOpen(items[0] ?? input, repo);
  }

  if (items.length > MAX_TARGETS) {
    return { error: getMessage("errTooManyPrs", { max: String(MAX_TARGETS) }) };
  }

  const problem = configProblem(await loadRules(), items.find(isBareNumber));

  return problem ? { error: problem } : openEach(items, repo);
}

// Closes id when it is still a duplicate of the PR it showed at the first scan.
async function closeDuplicate(id: TabId, key: PrKey | null | undefined): Promise<boolean> {
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

async function closeEachDuplicate(): Promise<DuplicatesClosed> {
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

// Keeps one tab per PR (the active one, else the oldest) and closes the rest; each close is re-
// decided from the current tabs.
export function closeDuplicates(): Promise<DuplicatesClosed> {
  return serial(closeEachDuplicate);
}

function isGroup(group: TabGroup | null): group is TabGroup {
  return group !== null;
}

async function findGroup(id: GroupId): Promise<TabGroup | null> {
  try {
    return await chrome.tabGroups.get(id);
  } catch {
    return null;
  }
}

async function groupsById(entries: PrEntry[]): Promise<Map<GroupId, TabGroup>> {
  const none = chrome.tabGroups.TAB_GROUP_ID_NONE;
  const ids = [...new Set(entries.map((entry) => entry.tab.groupId))].filter((id) => id !== none);
  const groups = await Promise.all(ids.map(findGroup));

  return new Map(groups.filter(isGroup).map((group) => [group.id, group]));
}

const metadataAttempts = new Map<PrKey, number>();
let metadataFlight: Promise<void> | undefined;
let metadataRevision: MetadataRevision = 0;

function needsMetadata(key: PrKey, meta: Meta): boolean {
  const recorded = meta[key];

  return !recorded?.title || !recorded.headRefName;
}

function metadataBatch(entries: PrEntry[], meta: Meta): PrRef[] {
  const byKey = new Map(entries.map((entry) => [entry.pr.key, entry.pr]));
  const batch: PrRef[] = [];
  const now = Date.now();

  for (const key of metadataAttempts.keys()) {
    if (!byKey.has(key)) {
      metadataAttempts.delete(key);
    }
  }

  for (const [key, pr] of byKey) {
    const retryAt = metadataAttempts.get(key) ?? 0;

    if (needsMetadata(key, meta) && now >= retryAt && batch.length < MAX_PRS_PER_POLL) {
      batch.push({ owner: pr.owner, repo: pr.repo, number: pr.number });
      metadataAttempts.set(key, now + SETTINGS_RETRY_MS);
    }
  }

  return batch;
}

async function hydrateMetadata(): Promise<void> {
  const rules = await loadRules();
  const batch = metadataBatch(await prEntries(rules), await readMeta());

  if (batch.length === 0) {
    return;
  }

  try {
    const reply = await askHost({ type: "states", prs: batch });
    await recordOwners(reply.owners);
    await updateMeta((meta) => recordStates(meta, reply.prs));
    await regroup(rules, await prEntries(rules));
  } finally {
    metadataRevision++;
  }
}

function handleMetadataSettled(): void {
  metadataFlight = undefined;
}

function backfillMissingMetadata(): Promise<void> {
  metadataFlight ??= hydrateMetadata().catch(warn).finally(handleMetadataSettled);

  return metadataFlight;
}

export async function summary(): Promise<Summary> {
  void backfillMissingMetadata();
  const entries = await prEntries(await loadRules());

  return summarize(entries, await groupsById(entries), await readMeta());
}

function branchRef(head: BranchRef): BranchRef {
  return { repo: head.repo, branch: head.branch };
}

export async function panes(heads: readonly BranchRef[]): Promise<PaneLookup> {
  const prs = heads.slice(0, MAX_PANE_LOOKUPS).map(branchRef);

  if (prs.length === 0) {
    return {};
  }

  const reply = await askHost({ type: "panes", prs });

  return reply.panes;
}

export async function focusPane(head: BranchRef): Promise<FocusedPane> {
  console.info("PR tabs focusPane send", branchRef(head));

  try {
    const reply = await askHost({ type: "focusPane", ...branchRef(head) });

    console.info("PR tabs focusPane reply", reply);

    return { pane: reply.pane };
  } catch (error) {
    console.warn("PR tabs focusPane failed", error);
    throw error;
  }
}

const pending = new Map<TabId, ReturnType<typeof setTimeout>>();

function handleTabSettled(tabId: TabId): void {
  pending.delete(tabId);
  void serial(() => groupTabId(tabId)).then(ensureAlarm);
}

function handleTabUpdated(tabId: TabId, change: chrome.tabs.OnUpdatedInfo, tab: Tab): void {
  if (!tabUpdateMatters(change, tab, prTabIds.has(tabId))) {
    return;
  }

  clearTimeout(pending.get(tabId));
  pending.set(tabId, setTimeout(handleTabSettled, GROUP_DEBOUNCE_MS, tabId));
}

function handleTabRemoved(tabId: TabId): void {
  clearTimeout(pending.get(tabId));
  pending.delete(tabId);
  ensureAlarm().catch(warn);
}

function handleAlarmFired(alarm: chrome.alarms.Alarm): void {
  if (alarm.name === ALARM) {
    void poll();
  }
}

function handleOpenAllMessage(): Promise<Reply<"openAll">> {
  return openAll();
}

function handleGoToMessage(message: RequestOf<"goTo">): Promise<Reply<"goTo">> {
  return goTo(message.input, message.repo);
}

function handleFocusPrMessage(message: RequestOf<"focusPr">): Promise<Reply<"focusPr">> {
  return focusPr(message.key);
}

function handleClosePrMessage(message: RequestOf<"closePr">): Promise<Reply<"closePr">> {
  return closePr(message.key, message.tabId);
}

function handleReposMessage(): Promise<RepoChoices> {
  return repoChoices();
}

function handlePickRepoMessage(message: RequestOf<"pickRepo">): Promise<void> {
  return pickRepo(message.repo);
}

function handleSummaryMessage(): Promise<Summary> {
  return summary();
}

function handleCheckNowMessage(): Promise<Reply<"checkNow">> {
  return checkNow();
}

function handleCloseDuplicatesMessage(): Promise<DuplicatesClosed> {
  return closeDuplicates();
}

function handlePanesMessage(message: RequestOf<"panes">): Promise<PaneLookup> {
  return panes(message.heads);
}

function handleFocusPaneMessage(message: RequestOf<"focusPane">): Promise<FocusedPane> {
  return focusPane(message.head);
}

const handleRuntimeMessage = messageListener({
  openAll: handleOpenAllMessage,
  openReviews,
  openingProgress,
  goTo: handleGoToMessage,
  focusPr: handleFocusPrMessage,
  closePr: handleClosePrMessage,
  repos: handleReposMessage,
  pickRepo: handlePickRepoMessage,
  summary: handleSummaryMessage,
  checkNow: handleCheckNowMessage,
  closeDuplicates: handleCloseDuplicatesMessage,
  panes: handlePanesMessage,
  focusPane: handleFocusPaneMessage,
});

async function groupOpenTabs(rules: Rules, meta: Meta): Promise<void> {
  for (const { tab, pr } of await prEntries(rules)) {
    await groupTab(tab, pr, meta).catch(warn);
  }
}

// Groups the PR tabs already open and polls soon; restored tabs may still be arriving, so the alarm
// is armed regardless.
async function start(): Promise<void> {
  await chrome.alarms.create(ALARM, { delayInMinutes: FIRST_POLL_SECONDS / 60 });

  void backfillMissingMetadata();
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
