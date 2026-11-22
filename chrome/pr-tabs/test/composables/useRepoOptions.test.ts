import assert from "node:assert/strict";
import { test } from "vitest";
import { useRepoOptions } from "#/composables/useRepoOptions";
import type { GroupSummary } from "#/types";

const groups: GroupSummary[] = [
  {
    id: "1",
    title: "web-app",
    color: "yellow",
    count: 1,
    prs: [{ tabId: 1, key: "acme/web-app#2972", label: "web-app#2972", title: "", head: null }],
  },
  {
    id: "2",
    title: "Platform",
    color: "blue",
    count: 1,
    prs: [{ tabId: 2, key: "acme/ops-platform#5", label: "ops-platform#5", title: "", head: null }],
  },
];

test("options put the guessed repo first and carry group colour and open tab", () => {
  const options = useRepoOptions({
    groups,
    repos: ["ops-platform", "web-app", "api"],
    guessed: "web-app",
    number: 2972,
  });

  assert.deepEqual(options.value, [
    { id: "repo-web-app", repo: "web-app", color: "yellow", open: "acme/web-app#2972" },
    { id: "repo-ops-platform", repo: "ops-platform", color: "blue", open: null },
    { id: "repo-api", repo: "api", color: "grey", open: null },
  ]);
});

test("no number, no options; an unlisted guess keeps the listed order", () => {
  assert.deepEqual(useRepoOptions({ groups, repos: ["a"], guessed: "a", number: null }).value, []);
  assert.deepEqual(
    useRepoOptions({ groups: [], repos: ["a", "b"], guessed: "z", number: 1 }).value.map(
      (option) => option.repo,
    ),
    ["a", "b"],
  );
});
