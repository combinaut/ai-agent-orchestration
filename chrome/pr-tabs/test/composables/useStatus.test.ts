import assert from "node:assert/strict";
import { test } from "vitest";
import { useStatus } from "#/composables/useStatus";

test("the status line starts empty and shows plain text or an error", () => {
  const { statusText, isStatusError, info, error } = useStatus();

  assert.equal(statusText.value, "");
  assert.equal(isStatusError.value, false);

  info("Checking…");

  assert.equal(statusText.value, "Checking…");
  assert.equal(isStatusError.value, false);

  error("gh: not logged in");

  assert.equal(statusText.value, "gh: not logged in");
  assert.equal(isStatusError.value, true);

  info("2 PRs in 1 group");

  assert.equal(isStatusError.value, false);
});

test("summary writes share a revision until a newer action report supersedes them", () => {
  const status = useStatus();
  status.info("Opening…");
  const revision = status.revision.value;
  status.info("2 PRs in 1 group", revision);
  status.info("3 PRs in 1 group", revision);

  assert.equal(status.statusText.value, "3 PRs in 1 group");
  assert.equal(status.revision.value, revision);

  status.info("Opened 3.");
  status.info("stale count", revision);
  status.error("stale error", revision);

  assert.equal(status.statusText.value, "Opened 3.");
  assert.equal(status.isStatusError.value, false);
});
