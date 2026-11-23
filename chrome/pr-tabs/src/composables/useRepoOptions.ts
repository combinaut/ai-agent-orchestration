import { computed, toValue } from "vue";
import { isGroupColor } from "#/core";
import type {
  GroupColor,
  GroupSummary,
  PrKey,
  PrNumber,
  RepoName,
  RepoOption,
  RepoOptionSources,
} from "#/types";

function repoOf(key: PrKey): RepoName {
  return key.split("#")[0]?.split("/").at(-1) ?? "";
}

function colorOf(groups: GroupSummary[], repo: RepoName): GroupColor {
  const group =
    groups.find((candidate) => candidate.title === repo) ??
    groups.find((candidate) => candidate.prs.some((row) => repoOf(row.key) === repo));

  return isGroupColor(group?.color) ? group.color : "grey";
}

function openKey(groups: GroupSummary[], repo: RepoName, number: PrNumber): PrKey | null {
  const rows = groups.flatMap((group) => group.prs);
  const row = rows.find((candidate) => candidate.key.endsWith(`/${repo}#${number}`));

  return row?.key ?? null;
}

// The go-to repository choices for a PR number: a repo with it open first, else the guessed one.
export function useRepoOptions({ groups, repos, guessed, number }: RepoOptionSources) {
  return computed<RepoOption[]>(() => {
    const target = toValue(number);
    const first = toValue(guessed);
    const listed = toValue(repos);

    if (target === null) {
      return [];
    }

    const ordered = listed.includes(first)
      ? [first, ...listed.filter((repo) => repo !== first)]
      : [...listed];

    const options = ordered.map((repo) => ({
      id: `repo-${repo}`,
      repo,
      color: colorOf(toValue(groups), repo),
      open: openKey(toValue(groups), repo, target),
    }));

    return options.toSorted((a, b) => Number(b.open !== null) - Number(a.open !== null));
  });
}
