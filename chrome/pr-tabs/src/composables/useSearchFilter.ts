import { computed, toValue, type MaybeRefOrGetter } from "vue";
import type { GroupSummary, MatchRange, PrRow, SearchFilter, SearchQuery } from "#/types";

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

function matchedRow(row: PrRow, query: SearchQuery): PrRow {
  const title = query.numberOnly ? [] : matchRanges(row.title, query.text);

  return {
    ...row,
    highlights: { number: matchRanges(prNumberText(row), query.text), title },
  };
}

function hasMatch(row: PrRow): boolean {
  return Boolean(row.highlights?.number.length || row.highlights?.title.length);
}

function matchedGroup(group: GroupSummary, query: SearchQuery): GroupSummary | null {
  const titleMatches = query.numberOnly ? [] : matchRanges(group.title, query.text);
  const rows = group.prs.map((row) => matchedRow(row, query));
  const prs = titleMatches.length > 0 ? rows : rows.filter(hasMatch);

  return prs.length > 0
    ? { ...group, prs, count: prs.length, total: group.prs.length, titleMatches }
    : null;
}

// Case-insensitive substring filter over PR number, title and group (repository) name.
export function useSearchFilter(
  groups: MaybeRefOrGetter<GroupSummary[]>,
  query: MaybeRefOrGetter<SearchQuery>,
): SearchFilter {
  const active = computed(() => toValue(query).text.trim().length > 0);
  const filtered = computed(() => {
    const current = toValue(query);
    const trimmed = { ...current, text: current.text.trim() };

    return active.value
      ? toValue(groups).flatMap((group) => matchedGroup(group, trimmed) ?? [])
      : toValue(groups);
  });
  const count = computed(() => filtered.value.reduce((total, group) => total + group.count, 0));

  return { groups: filtered, active, count };
}
