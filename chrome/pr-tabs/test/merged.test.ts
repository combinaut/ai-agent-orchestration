import assert from "node:assert/strict";
import { test } from "vitest";
import type { Entry, States, TabId, TabLike } from "#/types";
import { tabsToClose } from "#/core";

function tab(id: TabId, active = false): TabLike {
  return { id, active };
}

const SINCE = Date.parse("2026-01-02T00:00:00Z");

test("a finished PR keeps a tab it closed before seeing, without a visit or without a close time", () => {
  const entries: Entry[] = [
    { tab: tab(1), pr: { key: "acme/webapp#3284" } },
    { tab: tab(2), pr: { key: "acme/ai-agent-orchestration#17" } },
    { tab: tab(3), pr: { key: "acme/webapp#1" } },
    { tab: tab(4), pr: { key: "acme/webapp#2" } },
  ];

  const states: States = {
    "acme/webapp#3284": { state: "MERGED", closedAt: "2026-01-01T00:00:00Z" },
    "acme/ai-agent-orchestration#17": { state: "CLOSED", closedAt: "2026-01-01T12:00:00Z" },
    "acme/webapp#1": { state: "MERGED", closedAt: "2026-01-03T00:00:00Z" },
    "acme/webapp#2": { state: "MERGED", closedAt: null },
  };

  assert.deepEqual(
    tabsToClose(entries, states, {
      1: { key: "acme/webapp#3284", since: SINCE },
      2: { key: "acme/ai-agent-orchestration#17", since: SINCE },
      4: { key: "acme/webapp#2", since: SINCE },
    }),
    [],
  );
});
