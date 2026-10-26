import assert from "node:assert/strict";
import { test } from "vitest";
import { useStatus } from "#/composables/useStatus";
import { useSummary } from "#/composables/useSummary";
import type { Ask, GroupSummary } from "#/types";
import type { FakeReplies } from "#test/types";
import { fakeAsk } from "./fakeAsk";

const groups: GroupSummary[] = [{ title: "Finder", color: "yellow", count: 2, prs: [] }];

test("refresh shows the PR count and lists the groups", async () => {
  const status = useStatus();
  const { ask } = fakeAsk({ summary: { prs: 2, groups } });
  const summary = useSummary(ask, status);

  await summary.refresh();

  assert.equal(status.statusText.value, "2 PRs in 1 group");
  assert.equal(status.isStatusError.value, false);
  assert.deepEqual(summary.groups.value, groups);
});

test("a failed refresh shows the error and keeps the groups it had", async () => {
  const status = useStatus();
  const replies: FakeReplies = { summary: { prs: 2, groups } };
  const summary = useSummary(fakeAsk(replies).ask, status);

  await summary.refresh();
  replies.summary = { error: "No answer" };
  await summary.refresh();

  assert.equal(status.statusText.value, "No answer");
  assert.equal(status.isStatusError.value, true);
  assert.deepEqual(summary.groups.value, groups);
});

test("a final refresh queues behind an active progress refresh without overlap", async () => {
  const held = Promise.withResolvers<void>();
  const started = Promise.withResolvers<void>();
  const base = fakeAsk({ summary: { prs: 2, groups } });
  let active = 0;
  let maximum = 0;
  const ask: Ask = async (message) => {
    maximum = Math.max(maximum, ++active);
    started.resolve();
    await held.promise;
    active--;

    return base.ask(message);
  };
  const summary = useSummary(ask, useStatus());
  const first = summary.refresh();
  await started.promise;
  const final = summary.refresh();
  await new Promise((resolve) => setImmediate(resolve));
  held.resolve();
  await Promise.all([first, final]);

  assert.equal(maximum, 1);
  assert.equal(base.sent.length, 2);
});
