import assert from "node:assert/strict";
import { test } from "vitest";
import { useStatus } from "../../src/composables/useStatus";
import { useSummary } from "../../src/composables/useSummary";
import { fakeAsk, type FakeReplies } from "./fakeAsk";

const groups = [{ title: "Finder", color: "yellow", count: 2 }];

test("refresh shows the PR count and lists the groups", async () => {
  const status = useStatus();
  const { ask } = fakeAsk({ summary: { prs: 2, groups } });
  const summary = useSummary(ask, status);
  await summary.refresh();
  assert.deepEqual(status.status.value, { kind: "info", text: "2 PRs in 1 group" });
  assert.deepEqual(summary.groups.value, groups);
});

test("a failed refresh shows the error and keeps the groups it had", async () => {
  const status = useStatus();
  const replies: FakeReplies = { summary: { prs: 2, groups } };
  const summary = useSummary(fakeAsk(replies).ask, status);
  await summary.refresh();
  replies.summary = { error: "No answer" };
  await summary.refresh();
  assert.deepEqual(status.status.value, { kind: "error", text: "No answer" });
  assert.deepEqual(summary.groups.value, groups);
});
