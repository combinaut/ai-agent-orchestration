import assert from "node:assert/strict";
import { test } from "vitest";
import { useStatus } from "../../src/composables/useStatus";

test("the status line starts empty and shows plain text or an error", () => {
  const { status, statusText, isStatusError, info, error } = useStatus();
  assert.deepEqual(status.value, { kind: "info", text: "" });
  info("Checking…");
  assert.deepEqual(status.value, { kind: "info", text: "Checking…" });
  assert.equal(isStatusError.value, false);
  error("gh: not logged in");
  assert.deepEqual(status.value, { kind: "error", text: "gh: not logged in" });
  assert.equal(statusText.value, "gh: not logged in");
  assert.equal(isStatusError.value, true);
  info("2 PRs in 1 group");
  assert.equal(isStatusError.value, false);
});
