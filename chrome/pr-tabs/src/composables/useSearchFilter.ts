import Fuse, { type FuseResult, type IFuseOptions } from "fuse.js";
import { computed, toValue, type MaybeRefOrGetter } from "vue";
import type { GroupSummary, MatchRange, PrRow, SearchFilter, SearchPr, SearchQuery } from "#/types";

const FUSE_OPTIONS: IFuseOptions<SearchPr> = {
  keys: ["title", "label", "repo", "number", "branch", "groupName"],
  threshold: 0.35,
  includeScore: true,
  includeMatches: true,
  ignoreLocation: true,
};

export function prNumberText(row: Pick<PrRow, "key">): string {
  return `#${row.key.split("#").at(-1) ?? ""}`;
}

export function matchRanges(text: string, needle: string): MatchRange[] {
  const haystack = text.toLowerCase();
  const target = needle.toLowerCase();
  const found: MatchRange[] = [];

  if (!target) {
    return found;
  }

  for (let at = haystack.indexOf(target); at >= 0; at = haystack.indexOf(target, at + 1)) {
    found.push([at, at + target.length - 1]);
  }

  return found;
}

function searchablePr(row: PrRow, group: GroupSummary): SearchPr {
  const [repository, number] = row.key.split("#");

  return {
    row,
    group,
    label: row.label,
    title: row.title,
    repo: repository?.split("/").at(-1) ?? "",
    number: Number(number),
    branch: row.head?.branch ?? "",
    groupName: group.title,
  };
}

// Fuse also reports scattered short fragments; marks keep ranges of at least this length.
function shortest(queryLength: number): number {
  return Math.min(queryLength, 3);
}

function indicesOf(
  result: FuseResult<SearchPr>,
  key: keyof SearchPr,
  queryLength: number,
): MatchRange[] {
  const indices = result.matches?.find((match) => match.key === key)?.indices ?? [];

  return indices.filter((range) => range[1] - range[0] + 1 >= shortest(queryLength));
}

// Label ranges after the "#" of repo#number, moved onto the shown "#number".
function numberRanges(label: string, ranges: readonly MatchRange[]): MatchRange[] {
  const hash = label.lastIndexOf("#");

  return hash < 0
    ? []
    : ranges
        .filter((range) => range[1] >= hash)
        .map((range): MatchRange => [Math.max(range[0], hash) - hash, range[1] - hash]);
}

function groupFuzzyResults(results: FuseResult<SearchPr>[], queryLength: number): GroupSummary[] {
  const groups = new Map<string, GroupSummary>();

  for (const result of results) {
    const { group, row } = result.item;
    const highlights = {
      number: numberRanges(row.label, indicesOf(result, "label", queryLength)),
      title: indicesOf(result, "title", queryLength),
    };
    const matched = groups.get(group.id) ?? {
      ...group,
      prs: [],
      count: 0,
      total: group.prs.length,
      titleMatches: [],
    };

    matched.prs.push({ ...row, highlights });
    matched.count++;
    if (!matched.titleMatches?.length) {
      matched.titleMatches = indicesOf(result, "groupName", queryLength);
    }

    groups.set(group.id, matched);
  }

  return [...groups.values()];
}

function numberRow(row: PrRow, digits: string): PrRow {
  return { ...row, highlights: { number: matchRanges(prNumberText(row), digits), title: [] } };
}

function numberGroup(group: GroupSummary, digits: string): GroupSummary | null {
  const prs = group.prs
    .map((row) => numberRow(row, digits))
    .filter((row) => row.highlights?.number.length);

  return prs.length > 0 ? { ...group, prs, count: prs.length, total: group.prs.length } : null;
}

// Fuzzy search over PR title, label, repository, number, branch and group; number-only queries
// keep the PRs whose number contains the digits.
export function useSearchFilter(
  groups: MaybeRefOrGetter<GroupSummary[]>,
  query: MaybeRefOrGetter<SearchQuery>,
): SearchFilter {
  const text = computed(() => toValue(query).text.trim());
  const active = computed(() => text.value.length > 0);
  const fuse = computed(
    () =>
      new Fuse(
        toValue(groups).flatMap((group) => group.prs.map((row) => searchablePr(row, group))),
        FUSE_OPTIONS,
      ),
  );
  const filtered = computed(() => {
    if (!active.value) {
      return toValue(groups);
    }

    if (toValue(query).numberOnly) {
      return toValue(groups).flatMap((group) => numberGroup(group, text.value) ?? []);
    }

    return groupFuzzyResults(fuse.value.search(text.value), text.value.length);
  });
  const count = computed(() => filtered.value.reduce((total, group) => total + group.count, 0));

  return { groups: filtered, active, count };
}
