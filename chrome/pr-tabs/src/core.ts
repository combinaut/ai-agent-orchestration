// Pure PR tabs logic; background.ts owns every chrome.* call except catalog text read through i18n.ts.
import { getPlainMessage } from "#/i18n";
import type {
  BranchRef,
  Entry,
  GroupColor,
  GroupCount,
  GroupId,
  GroupLike,
  GroupPlacement,
  GroupSummary,
  GroupTitle,
  HeadKey,
  Meta,
  OpenAllPlan,
  Opened,
  OrgName,
  Owners,
  Planned,
  PollBatch,
  Pr,
  PrBranch,
  PrKey,
  PrNumber,
  PrRef,
  PrRow,
  PrState,
  PrStateName,
  RepoName,
  RepoNumbers,
  RepoRules,
  Rules,
  SearchResult,
  States,
  Summary,
  TabChange,
  TabId,
  TabLike,
  TabScan,
  TabVisit,
  TabVisits,
  UrlTab,
  WindowId,
} from "#/types";

export const POLL_SECONDS = 240;
export const FIRST_POLL_SECONDS = 30;
export const MAX_BACKOFF_SECONDS = 1800;
export const MAX_PRS_PER_POLL = 100;
export const OPEN_ALL_CAP = 30;
export const MAX_SEARCH_PAGES = 10;
export const MAX_PANE_LOOKUPS = 30;

const PR_URL =
  /^(?:https?:\/\/)?(?:www\.)?github\.com\/([A-Za-z0-9-]{1,39})\/([A-Za-z0-9_.-]{1,100})\/pull\/(\d{1,9})(?:[/?#]|$)/i;

// Keeps the URL's case.
function matchPrUrl(url: string | null | undefined): PrRef | null {
  const match = PR_URL.exec((url || "").trim());

  if (!match) {
    return null;
  }

  const [, owner = "", repo = "", number = ""] = match;

  return { owner, repo, number: Number(number) };
}

function makePr(ownerName: OrgName, repoName: RepoName, number: PrNumber): Pr {
  const owner = ownerName.toLowerCase();
  const repo = repoName.toLowerCase();

  return {
    owner,
    repo,
    number,
    key: `${owner}/${repo}#${number}`,
    url: `https://github.com/${owner}/${repo}/pull/${number}`,
  };
}

// The PR a github.com URL points at, any owner: lowercase owner/repo#number, whatever the sub-page,
// query or fragment; else null.
export function prKey(url: string | null | undefined): PrKey | null {
  const match = matchPrUrl(url);

  if (!match || match.number < 1) {
    return null;
  }

  return `${match.owner.toLowerCase()}/${match.repo.toLowerCase()}#${match.number}`;
}

export function isPrUrl(url: string | null | undefined): boolean {
  return PR_URL.test((url || "").trim());
}

// A tab update needs the grouping and alarm path: a URL change, or a load or title change on a PR
// tab or a tab known as one.
export function tabUpdateMatters(
  change: TabChange,
  tab: UrlTab | undefined,
  knownPrTab: boolean,
): boolean {
  if (change.url) {
    return true;
  }

  if (change.status !== "complete" && !change.title) {
    return false;
  }

  return isPrUrl(tab?.url) || knownPrTab;
}

// Matches the configured owner or an explicitly listed repository.
export function parsePrUrl(
  url: string | null | undefined,
  scope: OrgName | Pick<Rules, "org" | "repos"> | undefined,
): Pr | null {
  const match = matchPrUrl(url);

  if (!match || match.number < 1 || !repositoryInScope(match, scope)) {
    return null;
  }

  return makePr(match.owner, match.repo, match.number);
}

function repositoryInScope(
  pr: PrRef,
  scope: OrgName | Pick<Rules, "org" | "repos"> | undefined,
): boolean {
  const org = typeof scope === "string" ? scope : scope?.org;
  if (pr.owner.toLowerCase() === org?.toLowerCase()) {
    return true;
  }

  return (
    typeof scope === "object" &&
    Boolean(
      scope.repos?.some((repo) => repo.toLowerCase() === `${pr.owner}/${pr.repo}`.toLowerCase()),
    )
  );
}

// Repos a bare number can go to: defaultRepo first, then rules.repos, without case-insensitive
// repeats.
export function effectiveRepos(rules: RepoRules): RepoName[] {
  const repos: RepoName[] = [];

  for (const name of [rules.defaultRepo, ...(rules.repos || [])]) {
    if (name && !repos.some((seen) => seen.toLowerCase() === name.toLowerCase())) {
      repos.push(name);
    }
  }

  return repos;
}

// The repo in the list matching name (any case), else null.
function listedRepo(rules: RepoRules, name: RepoName | undefined): RepoName | null {
  return (
    effectiveRepos(rules).find((repo) => repo.toLowerCase() === (name || "").toLowerCase()) || null
  );
}

// The repo the popup selects: the last pick while it is still listed, else the first (defaultRepo).
export function pickedRepo(rules: RepoRules, stored?: RepoName): RepoName {
  return listedRepo(rules, stored) || effectiveRepos(rules)[0] || "";
}

const BARE_NUMBER = /^#?(\d{1,9})$/;

export function bareNumber(input: string): PrNumber | null {
  const match = BARE_NUMBER.exec(input.trim());
  const number = Number(match?.[1]);

  return number > 0 ? number : null;
}

export function isBareNumber(input: string): boolean {
  return bareNumber(input) !== null;
}

// Selected repo if newest >= target, else the smallest repo with newest >= target, else selected.
export interface RepoSuggestion {
  fallback: RepoName;
  newest: RepoNumbers;
}

export function suggestedRepo(
  repos: readonly RepoName[],
  target: PrNumber,
  { fallback, newest }: RepoSuggestion,
): RepoName {
  const newestOf = (repo: RepoName): number => newest[repo] ?? 0;
  const fits = (repo: RepoName): boolean => newestOf(repo) >= target;

  if (fits(fallback)) {
    return fallback;
  }

  const [smallest] = repos.filter(fits).toSorted((a, b) => newestOf(a) - newestOf(b));

  return smallest ?? fallback;
}

export const MAX_TARGETS = 20;

// The distinct items of a pasted list, split on commas, whitespace and new lines, in first-seen
// order.
export function splitTargets(input: string | undefined): string[] {
  return [...new Set((input || "").split(/[\s,]+/).filter(Boolean))];
}

// The popup offers repo options only when there is a choice.
export function showRepoPicker(rules: RepoRules): boolean {
  return effectiveRepos(rules).length >= 2;
}

const REPO_NUMBER = /^(?:([A-Za-z0-9-]{1,39})\/)?([A-Za-z0-9_.-]{1,100})#(\d{1,9})$/;

function prIn(org: OrgName, repo: RepoName | null, digits: string | undefined): Pr | null {
  const number = Number(digits);

  const [owner, name] = repo?.includes("/") ? repo.split("/") : [org, repo];

  return owner && name && number > 0 ? makePr(owner, name, number) : null;
}

function isTarget(text: string): boolean {
  return BARE_NUMBER.test(text) || REPO_NUMBER.test(text) || isPrUrl(text);
}

// Input the popup opens rather than searches: PR numbers, <repo>#<number> or PR URLs.
export function isGoToInput(input: string): boolean {
  const targets = splitTargets(input);

  return targets.length > 0 && targets.every(isTarget);
}

// The PR named by popup input: a number (#123 or 123) in the picked repo (default: pickedRepo),
// <repo>#<number> for a listed repo, or a PR URL.
export function parseTarget(
  input: string | undefined,
  rules: Pick<Rules, "org" | "defaultRepo" | "repos">,
  picked?: RepoName,
): Pr | null {
  const text = (input || "").trim();

  if (!rules.org) {
    return null;
  }

  const bare = BARE_NUMBER.exec(text);

  if (bare) {
    return prIn(rules.org, listedRepo(rules, picked) || pickedRepo(rules), bare[1]);
  }

  const named = REPO_NUMBER.exec(text);

  if (named) {
    return prIn(
      rules.org,
      listedRepo(rules, named[1] ? `${named[1]}/${named[2]}` : named[2]),
      named[3],
    );
  }

  return parsePrUrl(text, rules);
}

// The open tab for key, preferring one in the focused window.
export function findOpenTab<E extends Entry>(
  entries: E[],
  key: PrKey,
  focusedWindowId: WindowId | undefined,
): E | null {
  const matches = entries.filter((entry) => entry.pr.key === key);

  return matches.find((entry) => entry.tab.windowId === focusedWindowId) || matches[0] || null;
}

// Tab IDs to close so each PR keeps one tab: the active tab in the focused window, else any active
// tab, else the lowest ID (the oldest). Non-PR tabs are never listed.
export function duplicateTabIds(tabs: TabLike[], focusedWindowId?: WindowId): TabId[] {
  const byKey = new Map<PrKey, TabLike[]>();

  for (const tab of tabs) {
    const key = prKey(tab.url);

    if (key) {
      byKey.set(key, [...(byKey.get(key) || []), tab]);
    }
  }

  const rank = (tab: TabLike) => {
    if (!tab.active) {
      return 0;
    }

    return tab.windowId === focusedWindowId ? 2 : 1;
  };

  const ids: TabId[] = [];

  for (const group of byKey.values()) {
    const [keep] = group.toSorted((a, b) => rank(b) - rank(a) || a.id - b.id);
    ids.push(...group.filter((tab) => tab !== keep).map((tab) => tab.id));
  }

  return ids;
}

const GROUP_COLORS: readonly GroupColor[] = [
  "grey",
  "blue",
  "red",
  "yellow",
  "green",
  "pink",
  "purple",
  "cyan",
  "orange",
];

export function isGroupColor(name: string | undefined): name is GroupColor {
  return GROUP_COLORS.some((color) => color === name);
}

function leastUsedColor(uses: Map<GroupColor, number>): GroupColor {
  return GROUP_COLORS.toSorted((a, b) => (uses.get(a) ?? 0) - (uses.get(b) ?? 0))[0] ?? "grey";
}

export function allocateGroupColors(
  names: readonly GroupTitle[],
  previous: Record<GroupTitle, GroupColor>,
): Record<GroupTitle, GroupColor> {
  const next = new Map<GroupTitle, GroupColor>();
  const unique = [...new Set(names)].toSorted();
  const uses = new Map(GROUP_COLORS.map((color) => [color, 0]));

  for (const name of unique) {
    const color = previous[name];
    if (color && isGroupColor(color)) {
      next.set(name, color);
      uses.set(color, (uses.get(color) ?? 0) + 1);
    }
  }
  for (const name of unique) {
    if (next.has(name)) {
      continue;
    }
    const color = leastUsedColor(uses);
    next.set(name, color);
    uses.set(color, (uses.get(color) ?? 0) + 1);
  }

  return Object.fromEntries(next);
}

export function groupForPr(owners: Owners, pr: PrBranch): Pick<GroupPlacement, "name"> {
  const { owner, repo, number, branch = "", headRepository } = pr;
  const repository = (headRepository?.nameWithOwner || `${owner}/${repo}`).toLowerCase();
  const statusKey = (`${owner}/${repo}`.toLowerCase() + `#${number}`) as PrKey;
  const name =
    (headRepository !== null && owners[`${repository}:${branch}`]) || owners[statusKey] || repo;

  return { name };
}

// A PR tab may close once its PR is merged or closed, unless it is the tab in view.
export function shouldClose(tab: Pick<TabLike, "active">, state: PrStateName | undefined): boolean {
  return (state === "MERGED" || state === "CLOSED") && !tab.active;
}

// The tab still shows the PR it showed when the poll started.
export function stillShowsPr(
  tab: UrlTab | null | undefined,
  key: PrKey | undefined,
  org: OrgName | Pick<Rules, "org" | "repos">,
): boolean {
  return parsePrUrl(tab?.url, org)?.key === key;
}

export function closedDuringVisit(
  state: PrState | null | undefined,
  visit: TabVisit | undefined,
  key: PrKey,
): boolean {
  const closedAt = Date.parse(state?.closedAt ?? "");

  return visit?.key === key && closedAt > visit.since;
}

export function tabsToClose(entries: Entry[], states: States, visits: TabVisits): TabId[] {
  return entries
    .filter((entry) => {
      const state = states[entry.pr.key];

      return (
        shouldClose(entry.tab, state?.state) &&
        closedDuringVisit(state, visits[entry.tab.id], entry.pr.key)
      );
    })
    .map((entry) => entry.tab.id);
}

// Merges a tab scan: a visit newer than the scan wins, a scanned tab keeps a visit on its PR or
// starts one at now, a moved tab keeps its visit, and an older visit of a tab not found is dropped.
export function recordVisits(visits: TabVisits, scan: TabScan, now: number): TabVisits {
  const { entries, scannedAt, moved } = scan;
  const scanned = new Map(entries.map((entry) => [entry.tab.id, entry.pr.key]));
  const found = new Set([...scanned.keys(), ...moved]);
  const next: TabVisits = {};

  for (const [id, visit] of visitsOf(visits)) {
    if (found.has(id) || visit.since >= scannedAt) {
      next[id] = visit;
    }
  }

  for (const [id, key] of scanned) {
    const visit = next[id];

    if (!visit || (visit.key !== key && visit.since < scannedAt)) {
      next[id] = { key, since: now };
    }
  }

  return next;
}

// A tab that moves to another PR starts the given visit; one that leaves PRs has none.
export function navigateVisit(visits: TabVisits, id: TabId, visit: TabVisit | null): TabVisits {
  const next = { ...visits };

  if (visit === null) {
    delete next[id];
  } else if (next[id]?.key !== visit.key) {
    next[id] = visit;
  }

  return next;
}

function visitsOf(visits: TabVisits): [TabId, TabVisit][] {
  return Object.entries(visits).flatMap(([id, visit]) => (visit ? [[Number(id), visit]] : []));
}

function keysOf(record: Partial<Record<PrKey, unknown>>): PrKey[] {
  // Every key of a PrKey record is a PrKey.
  return Object.keys(record) as PrKey[];
}

// Metadata with each fetched state recorded.
export function recordStates(meta: Meta, states: States): Meta {
  const next = { ...meta };

  for (const key of keysOf(states)) {
    const state = states[key];

    if (state) {
      next[key] = state;
    }
  }

  return next;
}

// Metadata with an OPEN state for each opened PR that has none yet.
export function seedOpen(meta: Meta, opened: Opened[]): Meta {
  const next = { ...meta };

  for (const entry of opened) {
    const { hit, pr } = entry;

    next[pr.key] ??= {
      state: "OPEN",
      title: hit.title,
      headRefName: hit.headRefName,
      headRepository: hit.headRepository,
    };
  }

  return next;
}

export function pruneMeta(meta: Meta, keys: Set<PrKey>): Meta {
  const next: Meta = {};

  for (const key of keysOf(meta)) {
    if (keys.has(key)) {
      next[key] = meta[key];
    }
  }

  return next;
}

// Seconds to the next poll: POLL_SECONDS when healthy, else capped exponential backoff with jitter.
export function nextDelaySeconds(failures: number, random: () => number = Math.random): number {
  if (failures <= 0) {
    return POLL_SECONDS;
  }

  const ceiling = Math.min(MAX_BACKOFF_SECONDS, POLL_SECONDS * 2 ** failures);

  return Math.round(ceiling / 2 + (random() * ceiling) / 2);
}

// At most MAX_PRS_PER_POLL keys, rotating from cursor so every key is checked in turn.
export function pollBatch(keys: PrKey[], cursor: number): PollBatch {
  if (keys.length <= MAX_PRS_PER_POLL) {
    return { batch: keys, nextCursor: 0 };
  }

  const start = cursor % keys.length;
  const batch = [...keys.slice(start), ...keys.slice(0, start)].slice(0, MAX_PRS_PER_POLL);

  return { batch, nextCursor: (start + MAX_PRS_PER_POLL) % keys.length };
}

// The search hits to open (not already open, at most cap) and how many stay unopened; an estimate
// when results were left unfetched.
export function planOpenAll(search: SearchResult, openKeys: Set<PrKey>, org: OrgName): OpenAllPlan {
  const fresh = search.prs
    .map((hit) => ({ hit, pr: parsePrUrl(hit.url, org) }))
    .filter((planned): planned is Planned => !!planned.pr && !openKeys.has(planned.pr.key));
  const open = fresh.slice(0, OPEN_ALL_CAP);
  const unfetched = Math.max(0, search.total - search.prs.length);

  return { open, remaining: fresh.length - open.length + unfetched, estimated: unfetched > 0 };
}

function groupSummary(counted: GroupCount): GroupSummary {
  const prs = [...counted.prs.values()];

  return { id: counted.id, title: counted.title, color: counted.color, count: prs.length, prs };
}

function byCountThenTitle(a: GroupSummary, b: GroupSummary): number {
  return b.count - a.count || a.title.localeCompare(b.title);
}

// Lowercase repo, branch as is: the key the host replies under.
export function headKey(head: BranchRef): HeadKey {
  return `${head.repo.toLowerCase()}:${head.branch}`;
}

// The branch of a PR's head; null while unknown or when the head repository is gone.
export function prBranch(
  pr: Pick<Pr, "owner" | "repo">,
  state: PrState | undefined,
): BranchRef | null {
  if (!state?.headRefName || state.headRepository === null) {
    return null;
  }

  const repo = state.headRepository?.nameWithOwner || `${pr.owner}/${pr.repo}`;

  return { repo, branch: state.headRefName };
}

function prRow(pr: Pr, tabId: TabId, meta: Meta): PrRow {
  const state = meta[pr.key];

  return {
    key: pr.key,
    tabId,
    label: `${pr.repo}#${pr.number}`,
    title: state?.title ?? "",
    head: prBranch(pr, state),
  };
}

// Distinct heads of the listed PRs in list order, at most MAX_PANE_LOOKUPS.
export function lookupHeads(groups: GroupSummary[]): BranchRef[] {
  const heads = new Map<HeadKey, BranchRef>();

  for (const row of groups.flatMap((group) => group.prs)) {
    if (row.head && !heads.has(headKey(row.head))) {
      heads.set(headKey(row.head), row.head);
    }
  }

  return [...heads.values()].slice(0, MAX_PANE_LOOKUPS);
}

// Open PR count and, per tab group holding PR tabs, its title, colour and PRs.
export function summarize(
  entries: Entry<TabLike, Pr>[],
  groups: Map<GroupId | undefined, GroupLike>,
  meta: Meta,
): Summary {
  const byGroup = new Map<GroupId | undefined, GroupCount>();

  for (const entry of entries) {
    const { tab, pr } = entry;
    const group = groups.get(tab.groupId);

    if (!group) {
      continue;
    }

    const counted = byGroup.get(tab.groupId) ?? {
      id: String(tab.groupId),
      title: group.title || getPlainMessage("untitledGroup"),
      color: group.color,
      prs: new Map(),
    };

    counted.prs.set(pr.key, prRow(pr, tab.id, meta));
    byGroup.set(tab.groupId, counted);
  }

  const list = [...byGroup.values()].map(groupSummary).toSorted(byCountThenTitle);

  return { prs: new Set(entries.map((entry) => entry.pr.key)).size, groups: list };
}
