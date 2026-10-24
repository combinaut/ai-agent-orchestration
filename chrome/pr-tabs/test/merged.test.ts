import assert from "node:assert/strict";
import { test } from "vitest";
import { tabsToClose } from "../src/core";

const tab = (id: number, active = false) => ({ id, active });

test("a tab on a PR that merged or closed before the extension saw it open is closed", () => {
  const entries = [
    { tab: tab(1), pr: { key: "acme/webapp#3284" } },
    { tab: tab(2), pr: { key: "acme/ai-agent-orchestration#17" } },
  ];
  const states = {
    "acme/webapp#3284": { state: "MERGED" },
    "acme/ai-agent-orchestration#17": { state: "CLOSED" },
  };
  assert.deepEqual(tabsToClose(entries, states), [1, 2]);
});
