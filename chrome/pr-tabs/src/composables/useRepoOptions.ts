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

function matchesRepo(key: PrKey, repo: RepoName, listed: readonly RepoName[]): boolean {
  const full = key.split("#")[0]?.toLowerCase() ?? "";
  const name = repo.toLowerCase();
  if (name.includes("/")) {
    return full === name;
  }

  return full.endsWith(`/${name}`) && !listed.some((item) => item.toLowerCase() === full);
}

function colorOf(groups: GroupSummary[], repo: RepoName, listed: readonly RepoName[]): GroupColor {
  const group =
    groups.find((candidate) => candidate.title === repo) ??
    groups.find((candidate) => candidate.prs.some((row) => matchesRepo(row.key, repo, listed)));

  return isGroupColor(group?.color) ? group.color : "grey";
}

function openKey(
  groups: GroupSummary[],
  repo: RepoName,
  { number, listed }: { number: PrNumber; listed: readonly RepoName[] },
): PrKey | null {
  const rows = groups.flatMap((group) => group.prs);
  const row = rows.find(
    (candidate) => matchesRepo(candidate.key, repo, listed) && candidate.key.endsWith(`#${number}`),
  );

  return row?.key ?? null;
}

// Repository choices keep the background's recent-use order.
export function useRepoOptions({ groups, repos, number }: RepoOptionSources) {
  return computed<RepoOption[]>(() => {
    const target = toValue(number);
    const listed = toValue(repos);

    if (target === null) {
      return [];
    }

    const options = listed.map((repo) => ({
      id: `repo-${repo}`,
      repo,
      color: colorOf(toValue(groups), repo, listed),
      open: openKey(toValue(groups), repo, { number: target, listed }),
    }));

    return options;
  });
}
