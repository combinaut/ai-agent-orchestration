import assert from "node:assert/strict";
import { test } from "vitest";
import { useStatus } from "#/composables/useStatus";
import { useTabActions } from "#/composables/useTabActions";
import type { FakeReplies } from "#test/types";
import { fakeAsk } from "./fakeAsk";

function actions(replies: FakeReplies) {
  const status = useStatus();
  let refreshes = 0;

  async function refresh() {
    refreshes++;
    status.info("3 PRs in 2 groups");
  }

  const all = useTabActions({ ask: fakeAsk(replies).ask, status, refresh });

  return { ...all, status, refreshes: () => refreshes };
}

test("Open all says what is left, or adds the refreshed summary when nothing is", async () => {
  const more = actions({ openAll: { opened: 30, remaining: 12, estimated: true } });
  const running = more.openAll.run();

  assert.equal(more.openAll.busy.value, true);
  assert.equal(more.status.statusText.value, "Opening…");

  await running;

  assert.equal(more.openAll.busy.value, false);
  assert.equal(
    more.status.statusText.value,
    "Opened 30. about 12 more not opened; click again for the next batch.",
  );

  const done = actions({ openAll: { opened: 2, remaining: 0, estimated: false } });

  await done.openAll.run();

  assert.equal(done.status.statusText.value, "Opened 2. 3 PRs in 2 groups");
});

test("Close merged PRs and Close duplicate tabs report their result after a refresh", async () => {
  const all = actions({
    checkNow: {
      tabs: 2,
      prs: 2,
      checked: 2,
      closed: [{ key: "acme/webapp#1", state: "MERGED" }],
      inView: [],
    },
    closeDuplicates: { closed: 3 },
  });

  await all.checkNow.run();

  assert.equal(all.status.statusText.value, "Closed 1 merged tab.");

  await all.closeDuplicates.run();

  assert.equal(all.status.statusText.value, "Closed 3 duplicate PR tabs.");
  assert.equal(all.refreshes(), 2);
});

test("a failed action shows its error without a refresh", async () => {
  const all = actions({ checkNow: { error: "GitHub check failed: gh: not logged in" } });

  await all.checkNow.run();

  assert.equal(all.status.statusText.value, "GitHub check failed: gh: not logged in");
  assert.equal(all.status.isStatusError.value, true);
  assert.equal(all.checkNow.busy.value, false);
  assert.equal(all.refreshes(), 0);
});

test("Open reviews reports the shared batch result after refreshing", async () => {
  const all = actions({ openReviews: { opened: 1, remaining: 0, estimated: false } });

  await all.openReviews.run();

  assert.equal(all.status.statusText.value, "Opened 1. 3 PRs in 2 groups");
  assert.equal(all.openReviews.busy.value, false);
  assert.equal(all.refreshes(), 1);
});
