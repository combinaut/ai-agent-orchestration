// Pure PR tabs logic; background.ts owns every chrome.* call.
import type {
  CheckResult,
  Entry,
  GroupColor,
  GroupLike,
  GroupPlacement,
  GroupSummary,
  Meta,
  OpenAllPlan,
  OpenAllResult,
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
  PrStateName,
  RepoName,
  Rules,
  SearchResult,
  States,
  Summary,
  TabId,
  TabLike,
  WindowId,
} from "#/types";

type RepoRules = Pick<Rules, "defaultRepo" | "repos">;
type TabChange = Pick<chrome.tabs.OnUpdatedInfo, "url" | "status" | "title" | "groupId">;
type UrlTab = Pick<chrome.tabs.Tab, "url">;

interface GroupCount {
  title: string;
  color?: GroupColor;
  keys: Set<PrKey>;
}

export const POLL_SECONDS = 240;
export const FIRST_POLL_SECONDS = 30;
export const MAX_BACKOFF_SECONDS = 1800;
export const MAX_PRS_PER_POLL = 100;
export const OPEN_ALL_CAP = 30;
export const MAX_SEARCH_PAGES = 10;

const PR_URL =
  /^(?:https?:\/\/)?(?:www\.)?github\.com\/([A-Za-z0-9-]{1,39})\/([A-Za-z0-9_.-]{1,100})\/pull\/(\d{1,9})(?:[/?#]|$)/i;

// The owner, repo and number a PR URL names, as written; else null.
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
function listedRepo(rules: RepoRules, name: string | undefined): RepoName | null {
  return (
    effectiveRepos(rules).find((repo) => repo.toLowerCase() === (name || "").toLowerCase()) || null
  );
}

// The repo the popup selects: the last pick while it is still listed, else the first (defaultRepo).
export function pickedRepo(rules: RepoRules, stored?: RepoName): RepoName {
  return listedRepo(rules, stored) || effectiveRepos(rules)[0] || "";
}

// Popup input that is digits only, the one case the repo dropdown applies to.
export function isBareNumber(input: string): boolean {
  return /^\d+$/.test(input.trim());
}

// The popup shows a repo dropdown only when there is a choice.
export function showRepoPicker(rules: RepoRules): boolean {
  return effectiveRepos(rules).length >= 2;
}

const BARE_NUMBER = /^#?(\d{1,9})$/;
const REPO_NUMBER = /^([A-Za-z0-9_.-]{1,100})#(\d{1,9})$/;

// The PR number in repo, or null without a repo or for 0.
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

export function duplicateText(closed: number | undefined): string {
  if (!closed) {
    return "No duplicate PR tabs.";
  }

  return `Closed ${closed} duplicate PR tab${closed === 1 ? "" : "s"}.`;
}

export const GROUP_COLORS: readonly GroupColor[] = [
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
export function groupColor(name: string): GroupColor {
  let hash = 2166136261;

  for (const char of name) {
    hash = Math.imul(hash ^ (char.codePointAt(0) ?? 0), 16777619) >>> 0;
  }

  return GROUP_COLORS[hash % GROUP_COLORS.length] ?? "grey";
}

export function groupForPr(owners: Owners, pr: PrBranch): GroupPlacement {
  const { owner, repo, branch = "", headRepository } = pr;
  const repository = (headRepository?.nameWithOwner || `${owner}/${repo}`).toLowerCase();
  const name = (headRepository !== null && owners[`${repository}:${branch}`]) || repo;

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

// The PR keys of a record keyed by PR.
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
  return { title: counted.title, color: counted.color, count: counted.keys.size };
}

function byCountThenTitle(a: GroupSummary, b: GroupSummary): number {
  return b.count - a.count || a.title.localeCompare(b.title);
}

// Open PR count and, per tab group holding PR tabs, its title, colour and PR count.
export function summarize(entries: Entry[], groups: Map<number | undefined, GroupLike>): Summary {
  const byGroup = new Map<number | undefined, GroupCount>();

  for (const entry of entries) {
    const { tab, pr } = entry;
    const group = groups.get(tab.groupId);

    if (!group) {
      continue;
    }

    const counted = byGroup.get(tab.groupId) ?? {
      title: group.title || "Untitled",
      color: group.color,
      keys: new Set(),
    };

    counted.keys.add(pr.key);
    byGroup.set(tab.groupId, counted);
  }

  const list = [...byGroup.values()].map(groupSummary).toSorted(byCountThenTitle);

  return { prs: new Set(entries.map((entry) => entry.pr.key)).size, groups: list };
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function summaryText(summary: Summary): string {
  if (!summary.prs) {
    return "No PR tabs open";
  }

  return `${plural(summary.prs, "PR")} in ${plural(summary.groups.length, "group")}`;
}

// The popup line for a check result.
export function checkText(result: CheckResult): string {
  const { tabs, prs, checked, closed, inView } = result;

  if (!tabs) {
    return "No PR tabs open";
  }

  const merged = closed.filter((one) => one.state === "MERGED").length;
  const parts: string[] = [];

  if (merged) {
    parts.push(`${plural(merged, "merged tab")}`);
  }

  if (closed.length - merged) {
    parts.push(`${plural(closed.length - merged, "closed-PR tab")}`);
  }

  let text = parts.length
    ? `Closed ${parts.join(" and ")}`
    : `No merged or closed PRs among ${plural(checked, "PR")} checked`;

  if (inView.length) {
    text += `; ${plural(inView.length, "tab")} in view kept (${inView.join(", ")})`;
  }

  if (checked < prs) {
    text += `; ${prs - checked} more next time`;
  }

  return `${text}.`;
}

// The popup line after Open all: what is left, else the refreshed summary.
export function openAllText(result: OpenAllResult, summary: string): string {
  const { opened, remaining, estimated } = result;
  const done = `Opened ${opened}.`;

  if (!remaining) {
    return `${done} ${summary}`;
  }

  const about = estimated ? "about " : "";

  return `${done} ${about}${remaining} more not opened; click again for the next batch.`;
}
