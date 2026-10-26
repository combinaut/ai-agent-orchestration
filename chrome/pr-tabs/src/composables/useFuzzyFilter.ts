import { highlightedRow } from "#/highlight";
import { computed } from "vue";
import type { Ref } from "vue";
import { useFuse } from "@vueuse/integrations/useFuse";
import type { UseFuseOptions } from "@vueuse/integrations/useFuse";
import type { FuseResult } from "fuse.js";
import type { FuzzyFilter, GroupSummary, PrRow, SearchPr } from "#/types";

const SEARCH_OPTIONS: UseFuseOptions<SearchPr> = {
  matchAllWhenSearchEmpty: true,
  fuseOptions: {
    keys: ["title", "label", "repo", "number", "branch", "groupName"],
    threshold: 0.35,
    includeScore: true,
    includeMatches: true,
    ignoreLocation: true,
  },
};

function searchablePr(row: PrRow, group: GroupSummary): SearchPr {
  const [repository, number] = row.key.split("#");

  return {
    row,
    label: row.label,
    group,
    title: row.title,
    repo: repository?.split("/").at(-1) ?? "",
    number: Number(number),
    branch: row.head?.branch ?? "",
    groupName: group.title,
  };
}

function searchableRows(groups: GroupSummary[]): SearchPr[] {
  return groups.flatMap((group) => group.prs.map((row) => searchablePr(row, group)));
}

function groupResults(results: FuseResult<SearchPr>[], queryLength: number): GroupSummary[] {
  const groups = new Map<string, GroupSummary>();

  for (const result of results) {
    const { group } = result.item;
    const row = highlightedRow(result, queryLength);
    const matched = groups.get(group.id);

    if (matched) {
      matched.prs.push(row);
      matched.count++;
    } else {
      groups.set(group.id, { ...group, count: 1, prs: [row] });
    }
  }

  return [...groups.values()];
}

export function useFuzzyFilter(groups: Ref<GroupSummary[]>, query: Ref<string>): FuzzyFilter {
  const search = computed(() => query.value.trim());
  const indexed = computed(() => searchableRows(groups.value));
  const { results } = useFuse(search, indexed, SEARCH_OPTIONS);
  const active = computed(() => search.value.length > 0);
  const filtered = computed(() =>
    active.value ? groupResults(results.value, search.value.length) : groups.value,
  );
  const count = computed(() => results.value.length);
  const best = computed(() => results.value[0]?.item.row.key ?? null);

  return { groups: filtered, active, count, best };
}
