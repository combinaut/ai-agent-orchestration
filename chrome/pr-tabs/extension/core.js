// Pure PR tabs logic; background.js owns every chrome.* call.

export const POLL_SECONDS = 240;
export const FIRST_POLL_SECONDS = 30;
export const MAX_BACKOFF_SECONDS = 1800;
export const MAX_PRS_PER_POLL = 100;
export const OPEN_ALL_CAP = 30;
export const MAX_SEARCH_PAGES = 10;

const PR_URL = /^(?:https?:\/\/)?(?:www\.)?github\.com\/([A-Za-z0-9-]{1,39})\/([A-Za-z0-9_.-]{1,100})\/pull\/(\d{1,9})(?:[/?#]|$)/i;

function pr(ownerName, repoName, number) {
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
export function prKey(url) {
  const match = PR_URL.exec((url || "").trim());
  if (!match || Number(match[3]) < 1) return null;
  return `${match[1].toLowerCase()}/${match[2].toLowerCase()}#${Number(match[3])}`;
}

export function isPrUrl(url) {
  return PR_URL.test((url || "").trim());
}

// A tab update needs the grouping and alarm path: a URL change, or a load or title change on a PR tab or a tab known as one.
export function tabUpdateMatters(change, tab, knownPrTab) {
  if (change.url) return true;
  if (change.status !== "complete" && !change.title) return false;
  return isPrUrl(tab?.url) || knownPrTab;
}

// The PR a URL points at when it belongs to org, else null.
export function parsePrUrl(url, org) {
  const match = PR_URL.exec((url || "").trim());
  if (!org || !prKey(url) || match[1].toLowerCase() !== org.toLowerCase()) return null;
  return pr(org, match[2], Number(match[3]));
}

// Repos a bare number can go to: defaultRepo first, then rules.repos, without case-insensitive repeats.
export function effectiveRepos(rules) {
  const repos = [];
  for (const name of [rules.defaultRepo, ...(rules.repos || [])]) {
    if (name && !repos.some((seen) => seen.toLowerCase() === name.toLowerCase())) repos.push(name);
  }
  return repos;
}

// The repo in the list matching name (any case), else null.
function listedRepo(rules, name) {
  return effectiveRepos(rules).find((repo) => repo.toLowerCase() === (name || "").toLowerCase()) || null;
}

// The repo the popup selects: the last pick while it is still listed, else the first (defaultRepo).
export function pickedRepo(rules, stored) {
  return listedRepo(rules, stored) || effectiveRepos(rules)[0] || "";
}

// The popup shows a repo dropdown only when there is a choice.
export function showRepoPicker(rules) {
  return effectiveRepos(rules).length >= 2;
}

// The PR named by popup input: a number (#123 or 123) in the picked repo (default: pickedRepo), <repo>#<number> for a listed repo, or a PR URL.
export function parseTarget(input, rules, picked) {
  const text = (input || "").trim();
  if (!rules.org) return null;
  const number = /^#?(\d{1,9})$/.exec(text);
  if (number) {
    const repo = listedRepo(rules, picked) || pickedRepo(rules);
    return repo && Number(number[1]) > 0 ? pr(rules.org, repo, Number(number[1])) : null;
  }
  const named = /^([A-Za-z0-9_.-]{1,100})#(\d{1,9})$/.exec(text);
  if (named) {
    const repo = listedRepo(rules, named[1]);
    return repo && Number(named[2]) > 0 ? pr(rules.org, repo, Number(named[2])) : null;
  }
  return parsePrUrl(text, rules.org);
}

// The open tab for key, preferring one in the focused window.
export function findOpenTab(entries, key, focusedWindowId) {
  const matches = entries.filter((entry) => entry.pr.key === key);
  return matches.find((entry) => entry.tab.windowId === focusedWindowId) || matches[0] || null;
}

// Tab IDs to close so each PR keeps one tab: the active tab in the focused window, else any active tab, else the lowest ID (the oldest). Non-PR tabs are never listed.
export function duplicateTabIds(tabs, focusedWindowId) {
  const byKey = new Map();
  for (const tab of tabs) {
    const key = prKey(tab.url);
    if (key) byKey.set(key, [...(byKey.get(key) || []), tab]);
  }
  const ids = [];
  for (const group of byKey.values()) {
    const rank = (tab) => (tab.active ? (tab.windowId === focusedWindowId ? 2 : 1) : 0);
    const keep = [...group].sort((a, b) => rank(b) - rank(a) || a.id - b.id)[0];
    ids.push(...group.filter((tab) => tab !== keep).map((tab) => tab.id));
  }
  return ids;
}

export function duplicateText(closed) {
  if (!closed) return "No duplicate PR tabs.";
  return `Closed ${closed} duplicate PR tab${closed === 1 ? "" : "s"}.`;
}

const GROUP_COLORS = ["grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"];

// FNV-1a over the group name, independent of tab order and ownership refreshes.
export function groupColor(name) {
  let hash = 2166136261;
  for (const char of name) hash = Math.imul(hash ^ char.codePointAt(0), 16777619) >>> 0;
  return GROUP_COLORS[hash % GROUP_COLORS.length];
}

export function groupForPr(owners, { owner, repo, branch = "", headRepository }) {
  const repository = (headRepository?.nameWithOwner || `${owner}/${repo}`).toLowerCase();
  const name = (headRepository !== null && owners[`${repository}:${branch}`]) || repo;
  return { name, color: groupColor(name) };
}

// A PR tab closes once its PR is merged or closed, unless it is the tab in view.
export function shouldClose(tab, state) {
  return (state === "MERGED" || state === "CLOSED") && !tab.active;
}

// The tab still shows the PR it showed when the poll started.
export function stillShowsPr(tab, key, org) {
  return parsePrUrl(tab?.url, org)?.key === key;
}

export function tabsToClose(entries, states) {
  return entries.filter(({ tab, pr: { key } }) => shouldClose(tab, states[key]?.state)).map(({ tab }) => tab.id);
}

// Metadata with each fetched state recorded.
export function recordStates(meta, states) {
  const next = { ...meta };
  for (const [key, info] of Object.entries(states)) if (info) next[key] = info;
  return next;
}

// Metadata with an OPEN state for each opened PR that has none yet.
export function seedOpen(meta, opened) {
  const next = { ...meta };
  for (const { hit, pr } of opened) next[pr.key] ??= { state: "OPEN", title: hit.title, headRefName: hit.headRefName, headRepository: hit.headRepository };
  return next;
}

export function pruneMeta(meta, keys) {
  return Object.fromEntries(Object.entries(meta).filter(([key]) => keys.has(key)));
}

// Seconds to the next poll: POLL_SECONDS when healthy, else capped exponential backoff with jitter.
export function nextDelaySeconds(failures, random = Math.random) {
  if (failures <= 0) return POLL_SECONDS;
  const ceiling = Math.min(MAX_BACKOFF_SECONDS, POLL_SECONDS * 2 ** failures);
  return Math.round(ceiling / 2 + (random() * ceiling) / 2);
}

// At most MAX_PRS_PER_POLL keys, rotating from cursor so every key is checked in turn.
export function pollBatch(keys, cursor) {
  if (keys.length <= MAX_PRS_PER_POLL) return { batch: keys, nextCursor: 0 };
  const start = cursor % keys.length;
  const batch = [...keys.slice(start), ...keys.slice(0, start)].slice(0, MAX_PRS_PER_POLL);
  return { batch, nextCursor: (start + MAX_PRS_PER_POLL) % keys.length };
}

// The search hits to open (not already open, at most cap) and how many stay unopened; an estimate when results were left unfetched.
export function planOpenAll(search, openKeys, org, cap = OPEN_ALL_CAP) {
  const fresh = search.prs
    .map((hit) => ({ hit, pr: parsePrUrl(hit.url, org) }))
    .filter(({ pr }) => pr && !openKeys.has(pr.key));
  const open = fresh.slice(0, cap);
  const unfetched = Math.max(0, search.total - search.prs.length);
  return { open, remaining: fresh.length - open.length + unfetched, estimated: unfetched > 0 };
}

// Open PR count and, per tab group holding PR tabs, its title, colour and PR count.
export function summarize(entries, groups) {
  const byGroup = new Map();
  for (const { tab, pr } of entries) {
    const group = groups.get(tab.groupId);
    if (!group) continue;
    if (!byGroup.has(tab.groupId)) byGroup.set(tab.groupId, { title: group.title || "Untitled", color: group.color, keys: new Set() });
    byGroup.get(tab.groupId).keys.add(pr.key);
  }
  const list = [...byGroup.values()]
    .map(({ title, color, keys }) => ({ title, color, count: keys.size }))
    .sort((a, b) => b.count - a.count || a.title.localeCompare(b.title));
  return { prs: new Set(entries.map(({ pr }) => pr.key)).size, groups: list };
}

export function summaryText({ prs, groups }) {
  if (!prs) return "No PR tabs open";
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  return `${plural(prs, "PR")} in ${plural(groups.length, "group")}`;
}

// The popup line for a check result.
export function checkText({ tabs, prs, checked, closed, inView }) {
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  if (!tabs) return "No PR tabs open";
  const merged = closed.filter(({ state }) => state === "MERGED").length;
  const parts = [];
  if (merged) parts.push(`${plural(merged, "merged tab")}`);
  if (closed.length - merged) parts.push(`${plural(closed.length - merged, "closed-PR tab")}`);
  let text = parts.length ? `Closed ${parts.join(" and ")}` : `No merged or closed PRs among ${plural(checked, "PR")} checked`;
  if (inView.length) text += `; ${plural(inView.length, "tab")} in view kept (${inView.join(", ")})`;
  if (checked < prs) text += `; ${prs - checked} more next time`;
  return `${text}.`;
}
