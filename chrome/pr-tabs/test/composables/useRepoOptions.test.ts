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

test("options retain recent order despite an open repo and carry colour and tab", () => {
  const options = useRepoOptions({
    groups,
    repos: ["ops-platform", "web-app", "api"],
    number: 2972,
  });

  assert.deepEqual(options.value, [
    { id: "repo-ops-platform", repo: "ops-platform", color: "blue", open: null },
    { id: "repo-web-app", repo: "web-app", color: "yellow", open: "acme/web-app#2972" },
    { id: "repo-api", repo: "api", color: "grey", open: null },
  ]);
});

test("no number, no options; otherwise keep the listed order", () => {
  assert.deepEqual(useRepoOptions({ groups, repos: ["a"], number: null }).value, []);
  assert.deepEqual(
    useRepoOptions({ groups: [], repos: ["a", "b"], number: 1 }).value.map((option) => option.repo),
    ["a", "b"],
  );
});

test("qualified repos do not lend their open tabs to a same-name configured repo", () => {
  const external: GroupSummary[] = [
    {
      id: "3",
      title: "tools",
      color: "cyan",
      count: 1,
      prs: [{ tabId: 3, key: "other/web-app#2972", label: "web-app#2972", title: "", head: null }],
    },
  ];
  const options = useRepoOptions({
    groups: external,
    repos: ["web-app", "other/web-app"],
    number: 2972,
  });
  assert.deepEqual(
    options.value.map((option) => option.open),
    [null, "other/web-app#2972"],
  );
  assert.deepEqual(
    options.value.map((option) => option.color),
    ["grey", "cyan"],
  );
});
