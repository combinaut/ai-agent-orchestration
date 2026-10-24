// Pure PR tabs logic; background.ts owns every chrome.* call.

export const POLL_SECONDS = 240;
export const FIRST_POLL_SECONDS = 30;
export const MAX_BACKOFF_SECONDS = 1800;
export const MAX_PRS_PER_POLL = 100;
export const OPEN_ALL_CAP = 30;
export const MAX_SEARCH_PAGES = 10;

export interface Rules {
  org: string;
  defaultRepo: string;
  openAll: string;
  repos?: string[];
}

export interface Pr {
  owner: string;
  repo: string;
  number: number;
  key: string;
  url: string;
}

export interface TabLike {
  id: number;
  url?: string;
  active?: boolean;
  windowId?: number;
  groupId?: number;
}

export interface Entry<T extends TabLike = TabLike> {
  tab: T;
  pr: Pick<Pr, "key">;
}

export interface HeadRepository {
  nameWithOwner?: string;
}

export interface PrState {
  state?: string;
  title?: string;
  headRefName?: string;
  headRepository?: HeadRepository | null;
}

export type States = Record<string, PrState | null | undefined>;
export type Meta = Record<string, PrState>;
export type Owners = Record<string, string>;

export interface SearchHit {
  url: string;
  title?: string;
  headRefName?: string;
  headRepository?: HeadRepository | null;
}

export interface Planned {
  hit: SearchHit;
  pr: Pr;
}

export interface Opened {
  hit: Omit<SearchHit, "url">;
  pr: Pick<Pr, "key">;
}

export interface GroupLike {
  title?: string;
  color?: string;
}

export interface GroupSummary {
  title: string;
  color?: string;
  count: number;
}

export interface Summary {
  prs: number;
  groups: GroupSummary[];
}

export interface Closed {
  key: string;
  state?: string;
}

export interface CheckResult {
  tabs: number;
  prs: number;
  checked: number;
  closed: Closed[];
  inView: string[];
}

const PR_URL =
  /^(?:https?:\/\/)?(?:www\.)?github\.com\/([A-Za-z0-9-]{1,39})\/([A-Za-z0-9_.-]{1,100})\/pull\/(\d{1,9})(?:[/?#]|$)/i;

// The owner, repo and number a PR URL names, as written; else null.
function matchPrUrl(
  url: string | null | undefined,
): { owner: string; repo: string; number: number } | null {
  const match = PR_URL.exec((url || "").trim());
  if (!match) {
    return null;
  }
  const [, owner = "", repo = "", number = ""] = match;
  return { owner, repo, number: Number(number) };
}

function makePr(ownerName: string, repoName: string, number: number): Pr {
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

// The PR a github.com URL points at, any owner: lowercase owner/repo#number, whatever the sub-page, query or fragment; else null.
export function prKey(url: string | null | undefined): string | null {
  const match = matchPrUrl(url);
  if (!match || match.number < 1) {
    return null;
  }
  return `${match.owner.toLowerCase()}/${match.repo.toLowerCase()}#${match.number}`;
}

export function isPrUrl(url: string | null | undefined): boolean {
  return PR_URL.test((url || "").trim());
}

// A tab update needs the grouping and alarm path: a URL change, or a load or title change on a PR tab or a tab known as one.
export function tabUpdateMatters(
  change: { url?: string; status?: string; title?: string; groupId?: number },
  tab: { url?: string } | undefined,
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
export function parsePrUrl(url: string | null | undefined, org: string | undefined): Pr | null {
  const match = matchPrUrl(url);
  if (!org || !match || match.number < 1 || match.owner.toLowerCase() !== org.toLowerCase()) {
    return null;
  }
  return makePr(org, match.repo, match.number);
}

// Repos a bare number can go to: defaultRepo first, then rules.repos, without case-insensitive repeats.
export function effectiveRepos(rules: Pick<Rules, "defaultRepo" | "repos">): string[] {
  const repos: string[] = [];
  for (const name of [rules.defaultRepo, ...(rules.repos || [])]) {
    if (name && !repos.some((seen) => seen.toLowerCase() === name.toLowerCase())) {
      repos.push(name);
    }
  }
  return repos;
}

// The repo in the list matching name (any case), else null.
function listedRepo(
  rules: Pick<Rules, "defaultRepo" | "repos">,
  name: string | undefined,
): string | null {
  return (
    effectiveRepos(rules).find((repo) => repo.toLowerCase() === (name || "").toLowerCase()) || null
  );
}

// The repo the popup selects: the last pick while it is still listed, else the first (defaultRepo).
export function pickedRepo(rules: Pick<Rules, "defaultRepo" | "repos">, stored?: string): string {
  return listedRepo(rules, stored) || effectiveRepos(rules)[0] || "";
}

// Popup input that is digits only, the one case the repo dropdown applies to.
export function isBareNumber(input: string): boolean {
  return /^\d+$/.test(input.trim());
}

// The popup shows a repo dropdown only when there is a choice.
export function showRepoPicker(rules: Pick<Rules, "defaultRepo" | "repos">): boolean {
  return effectiveRepos(rules).length >= 2;
}

const BARE_NUMBER = /^#?(\d{1,9})$/;
const REPO_NUMBER = /^([A-Za-z0-9_.-]{1,100})#(\d{1,9})$/;

// The PR number in repo, or null without a repo or for 0.
function prIn(org: string, repo: string | null, digits: string | undefined): Pr | null {
  const number = Number(digits);
  return repo && number > 0 ? makePr(org, repo, number) : null;
}

// The PR named by popup input: a number (#123 or 123) in the picked repo (default: pickedRepo), <repo>#<number> for a listed repo, or a PR URL.
export function parseTarget(
  input: string | undefined,
  rules: Pick<Rules, "org" | "defaultRepo" | "repos">,
  picked?: string,
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
  key: string,
  focusedWindowId: number | undefined,
): E | null {
  const matches = entries.filter((entry) => entry.pr.key === key);
  return matches.find((entry) => entry.tab.windowId === focusedWindowId) || matches[0] || null;
}

// Tab IDs to close so each PR keeps one tab: the active tab in the focused window, else any active tab, else the lowest ID (the oldest). Non-PR tabs are never listed.
export function duplicateTabIds(tabs: TabLike[], focusedWindowId?: number): number[] {
  const byKey = new Map<string, TabLike[]>();
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
  const ids: number[] = [];
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

export const GROUP_COLORS = [
  "grey",
  "blue",
  "red",
  "yellow",
  "green",
  "pink",
  "purple",
  "cyan",
  "orange",
] as const;
export type GroupColor = (typeof GROUP_COLORS)[number];

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

export function groupForPr(
  owners: Owners,
  {
    owner,
    repo,
    branch = "",
    headRepository,
  }: { owner: string; repo: string; branch?: string; headRepository?: HeadRepository | null },
): { name: string; color: GroupColor } {
  const repository = (headRepository?.nameWithOwner || `${owner}/${repo}`).toLowerCase();
  const name = (headRepository !== null && owners[`${repository}:${branch}`]) || repo;
  return { name, color: groupColor(name) };
}

// A PR tab closes once its PR is merged or closed, unless it is the tab in view.
export function shouldClose(tab: { active?: boolean }, state: string | undefined): boolean {
  return (state === "MERGED" || state === "CLOSED") && !tab.active;
}

// The tab still shows the PR it showed when the poll started.
export function stillShowsPr(
  tab: { url?: string } | null | undefined,
  key: string | undefined,
  org: string,
): boolean {
  return parsePrUrl(tab?.url, org)?.key === key;
}

export function tabsToClose(entries: Entry[], states: States): number[] {
  return entries
    .filter(({ tab, pr: { key } }) => shouldClose(tab, states[key]?.state))
    .map(({ tab }) => tab.id);
}

// Metadata with each fetched state recorded.
export function recordStates(meta: Meta, states: States): Meta {
  const next = { ...meta };
  for (const [key, info] of Object.entries(states)) {
    if (info) {
      next[key] = info;
    }
  }
  return next;
}

// Metadata with an OPEN state for each opened PR that has none yet.
export function seedOpen(meta: Meta, opened: Opened[]): Meta {
  const next = { ...meta };
  for (const { hit, pr } of opened) {
    next[pr.key] ??= {
      state: "OPEN",
      title: hit.title,
      headRefName: hit.headRefName,
      headRepository: hit.headRepository,
    };
  }
  return next;
}

export function pruneMeta(meta: Meta, keys: Set<string>): Meta {
  return Object.fromEntries(Object.entries(meta).filter(([key]) => keys.has(key)));
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
export function pollBatch(keys: string[], cursor: number): { batch: string[]; nextCursor: number } {
  if (keys.length <= MAX_PRS_PER_POLL) {
    return { batch: keys, nextCursor: 0 };
  }
  const start = cursor % keys.length;
  const batch = [...keys.slice(start), ...keys.slice(0, start)].slice(0, MAX_PRS_PER_POLL);
  return { batch, nextCursor: (start + MAX_PRS_PER_POLL) % keys.length };
}

// The search hits to open (not already open, at most cap) and how many stay unopened; an estimate when results were left unfetched.
export function planOpenAll(
  search: { total: number; prs: SearchHit[] },
  openKeys: Set<string>,
  org: string,
): { open: Planned[]; remaining: number; estimated: boolean } {
  const fresh = search.prs
    .map((hit) => ({ hit, pr: parsePrUrl(hit.url, org) }))
    .filter((planned): planned is Planned => !!planned.pr && !openKeys.has(planned.pr.key));
  const open = fresh.slice(0, OPEN_ALL_CAP);
  const unfetched = Math.max(0, search.total - search.prs.length);
  return { open, remaining: fresh.length - open.length + unfetched, estimated: unfetched > 0 };
}

// Open PR count and, per tab group holding PR tabs, its title, colour and PR count.
export function summarize(entries: Entry[], groups: Map<number | undefined, GroupLike>): Summary {
  const byGroup = new Map<
    number | undefined,
    { title: string; color?: string; keys: Set<string> }
  >();
  for (const { tab, pr } of entries) {
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
  const list = [...byGroup.values()]
    .map(({ title, color, keys }) => ({ title, color, count: keys.size }))
    .toSorted((a, b) => b.count - a.count || a.title.localeCompare(b.title));
  return { prs: new Set(entries.map(({ pr }) => pr.key)).size, groups: list };
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function summaryText({ prs, groups }: { prs: number; groups: unknown[] }): string {
  if (!prs) {
    return "No PR tabs open";
  }
  return `${plural(prs, "PR")} in ${plural(groups.length, "group")}`;
}

// The popup line for a check result.
export function checkText({ tabs, prs, checked, closed, inView }: CheckResult): string {
  if (!tabs) {
    return "No PR tabs open";
  }
  const merged = closed.filter(({ state }) => state === "MERGED").length;
  const parts = [];
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

export interface OpenAllResult {
  opened: number;
  remaining: number;
  estimated: boolean;
}

// The popup line after Open all: what is left, else the refreshed summary.
export function openAllText(
  { opened, remaining, estimated }: OpenAllResult,
  summary: string,
): string {
  const done = `Opened ${opened}.`;
  if (!remaining) {
    return `${done} ${summary}`;
  }
  const about = estimated ? "about " : "";
  return `${done} ${about}${remaining} more not opened; click again for the next batch.`;
}
