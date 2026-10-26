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
  RepoRules,
  Rules,
  SearchResult,
  States,
  Summary,
  TabChange,
  TabId,
  TabLike,
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

// The PR a URL points at when it belongs to org, else null.
export function parsePrUrl(url: string | null | undefined, org: OrgName | undefined): Pr | null {
  const match = matchPrUrl(url);

  if (!org || !match || match.number < 1 || match.owner.toLowerCase() !== org.toLowerCase()) {
    return null;
  }

  return makePr(org, match.repo, match.number);
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

export function isBareNumber(input: string): boolean {
  return /^\d+$/.test(input.trim());
}

// The popup shows a repo dropdown only when there is a choice.
export function showRepoPicker(rules: RepoRules): boolean {
  return effectiveRepos(rules).length >= 2;
}

const BARE_NUMBER = /^#?(\d{1,9})$/;
const REPO_NUMBER = /^([A-Za-z0-9_.-]{1,100})#(\d{1,9})$/;

function prIn(org: OrgName, repo: RepoName | null, digits: string | undefined): Pr | null {
  const number = Number(digits);

  return repo && number > 0 ? makePr(org, repo, number) : null;
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
    return prIn(rules.org, listedRepo(rules, named[1]), named[2]);
  }

  return parsePrUrl(text, rules.org);
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

// FNV-1a over the group name, independent of tab order and ownership refreshes.
export function groupColor(name: GroupTitle): GroupColor {
  let hash = 2166136261;

  for (const char of name) {
    hash = Math.imul(hash ^ (char.codePointAt(0) ?? 0), 16777619) >>> 0;
  }

  return GROUP_COLORS[hash % GROUP_COLORS.length] ?? "grey";
}

export function groupForPr(owners: Owners, pr: PrBranch): GroupPlacement {
  const { owner, repo, number, branch = "", headRepository } = pr;
  const repository = (headRepository?.nameWithOwner || `${owner}/${repo}`).toLowerCase();
  const statusKey = (`${owner}/${repo}`.toLowerCase() + `#${number}`) as PrKey;
  const name =
    (headRepository !== null && owners[`${repository}:${branch}`]) || owners[statusKey] || repo;

  return { name, color: groupColor(name) };
}

// A PR tab closes once its PR is merged or closed, unless it is the tab in view.
export function shouldClose(tab: Pick<TabLike, "active">, state: PrStateName | undefined): boolean {
  return (state === "MERGED" || state === "CLOSED") && !tab.active;
}

// The tab still shows the PR it showed when the poll started.
export function stillShowsPr(
  tab: UrlTab | null | undefined,
  key: PrKey | undefined,
  org: OrgName,
): boolean {
  return parsePrUrl(tab?.url, org)?.key === key;
}

export function tabsToClose(entries: Entry[], states: States): TabId[] {
  return entries
    .filter((entry) => shouldClose(entry.tab, states[entry.pr.key]?.state))
    .map((entry) => entry.tab.id);
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

  return { title: counted.title, color: counted.color, count: prs.length, prs };
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

function prRow(pr: Pr, meta: Meta): PrRow {
  const state = meta[pr.key];

  return {
    key: pr.key,
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
      title: group.title || getPlainMessage("untitledGroup"),
      color: group.color,
      prs: new Map(),
    };

    counted.prs.set(pr.key, prRow(pr, meta));
    byGroup.set(tab.groupId, counted);
  }

  const list = [...byGroup.values()].map(groupSummary).toSorted(byCountThenTitle);

  return { prs: new Set(entries.map((entry) => entry.pr.key)).size, groups: list };
}
