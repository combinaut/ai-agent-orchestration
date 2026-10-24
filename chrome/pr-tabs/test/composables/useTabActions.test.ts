import assert from "node:assert/strict";
import { test } from "vitest";
import { useStatus } from "../../src/composables/useStatus";
import { useTabActions } from "../../src/composables/useTabActions";
import { fakeAsk, type FakeReplies } from "./fakeAsk";

function actions(replies: FakeReplies) {
  const status = useStatus();
  let refreshes = 0;
  async function refresh() {
    refreshes++;
    status.info("3 PRs in 2 groups");
  }
  const all = useTabActions({ ask: fakeAsk(replies).ask, status, refresh });
  return { ...all, status: status.status, refreshes: () => refreshes };
}

test("Open all says what is left, or adds the refreshed summary when nothing is", async () => {
  const more = actions({ openAll: { opened: 30, remaining: 12, estimated: true } });
  const running = more.openAll.run();
  assert.equal(more.openAll.busy.value, true);
  assert.equal(more.status.value.text, "Opening…");
  await running;
  assert.equal(more.openAll.busy.value, false);
  assert.equal(
    more.status.value.text,
    "Opened 30. about 12 more not opened; click again for the next batch.",
  );
  const done = actions({ openAll: { opened: 2, remaining: 0, estimated: false } });
  await done.openAll.run();
  assert.equal(done.status.value.text, "Opened 2. 3 PRs in 2 groups");
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
  assert.equal(all.status.value.text, "Closed 1 merged tab.");
  await all.closeDuplicates.run();
  assert.equal(all.status.value.text, "Closed 3 duplicate PR tabs.");
  assert.equal(all.refreshes(), 2);
});

test("a failed action shows its error without a refresh", async () => {
  const all = actions({ checkNow: { error: "GitHub check failed: gh: not logged in" } });
  await all.checkNow.run();
  assert.deepEqual(all.status.value, {
    kind: "error",
    text: "GitHub check failed: gh: not logged in",
  });
  assert.equal(all.checkNow.busy.value, false);
  assert.equal(all.refreshes(), 0);
});
