import { effectiveRepos } from "#/core";
import type { Rules } from "#/types";

export function validRepository(value: string): boolean {
  return (
    /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\/[A-Za-z0-9_.-]{1,100}$/.test(value) &&
    ![".", ".."].includes(value.split("/")[1] ?? "")
  );
}

export function repositoryName(repo: string, org: string): string {
  return (repo.includes("/") ? repo : `${org}/${repo}`).toLowerCase();
}

export function configuredRepositories(rules: Rules): string[] {
  return effectiveRepos(rules).map((repo) => repositoryName(repo, rules.org));
}

export function recentRepositories(
  repos: string[],
  usage: Record<string, number>,
  org: string,
): string[] {
  return repos.toSorted(
    (a, b) =>
      (usage[repositoryName(b, org)] ?? 0) - (usage[repositoryName(a, org)] ?? 0) ||
      a.localeCompare(b),
  );
}
