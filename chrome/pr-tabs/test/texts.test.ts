import assert from "node:assert/strict";
import { test } from "vitest";
import { checkLine, duplicateLine, openAllLine } from "#/texts";
import type { Closed } from "#/types";

const CLOSED: Closed[] = [{ key: "acme/webapp#1", state: "MERGED" }];

test("every status line carries a severity", () => {
  const check = { tabs: 2, prs: 2, checked: 2, closed: [], inView: [] };

  assert.equal(checkLine({ ...check, tabs: 0, prs: 0, checked: 0 }).kind, "info");
  assert.equal(checkLine(check).kind, "info");
  assert.equal(checkLine({ ...check, closed: CLOSED }).kind, "success");
  assert.equal(checkLine({ ...check, checked: 1 }).kind, "warning");
  assert.equal(duplicateLine(0).kind, "info");
  assert.equal(duplicateLine(2).kind, "success");
  assert.equal(openAllLine({ opened: 3, remaining: 0, estimated: false }, "").kind, "success");
  assert.equal(openAllLine({ opened: 3, remaining: 2, estimated: false }, "").kind, "warning");
});
