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
