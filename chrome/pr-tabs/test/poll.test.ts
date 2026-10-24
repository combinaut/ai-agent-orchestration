import assert from "node:assert/strict";
import { test } from "vitest";
import {
  FIRST_POLL_SECONDS,
  MAX_BACKOFF_SECONDS,
  MAX_PRS_PER_POLL,
  nextDelaySeconds,
  OPEN_ALL_CAP,
  planOpenAll,
  POLL_SECONDS,
  pollBatch,
} from "../src/core";

test("the first poll after load or startup comes within a minute", () => {
  assert.ok(FIRST_POLL_SECONDS >= 30 && FIRST_POLL_SECONDS <= 60);
});

test("a healthy poll waits between 3 and 5 minutes", () => {
  assert.ok(POLL_SECONDS >= 180 && POLL_SECONDS <= 300);
  assert.equal(nextDelaySeconds(0), POLL_SECONDS);
});

test("failures back off exponentially with jitter, capped", () => {
  assert.equal(
    nextDelaySeconds(1, () => 0),
    POLL_SECONDS,
  );
  assert.equal(
    nextDelaySeconds(1, () => 1),
    POLL_SECONDS * 2,
  );
  assert.equal(
    nextDelaySeconds(2, () => 1),
    POLL_SECONDS * 4,
  );
  assert.ok(nextDelaySeconds(2, () => 0.5) < nextDelaySeconds(2, () => 0.9));
  for (const failures of [5, 10, 50]) {
    assert.equal(
      nextDelaySeconds(failures, () => 1),
      MAX_BACKOFF_SECONDS,
    );
    assert.equal(
      nextDelaySeconds(failures, () => 0),
      MAX_BACKOFF_SECONDS / 2,
    );
  }
});

test("one poll asks for at most the cap and rotates through every key", () => {
  const keys = Array.from({ length: 250 }, (_, i) => `acme/webapp#${i}`);
  const seen = new Set();
  let cursor = 0;
  for (let round = 0; round < 3; round++) {
    const { batch, nextCursor } = pollBatch(keys, cursor);
    assert.equal(batch.length, MAX_PRS_PER_POLL);
    batch.forEach((key: string) => seen.add(key));
    cursor = nextCursor;
  }
  assert.equal(seen.size, keys.length);
  assert.deepEqual(pollBatch(keys.slice(0, 3), 7).batch, keys.slice(0, 3));
});

const hit = (repo: string, n: number) => ({
  url: `https://github.com/acme/${repo}/pull/${n}`,
  title: `PR ${n}`,
  headRefName: `b${n}`,
});

test("open all skips open PRs, opens at most the cap and counts the rest", () => {
  const prs = Array.from({ length: 40 }, (_, i) => hit("webapp", i + 1));
  const plan = planOpenAll({ total: 45, prs }, new Set(["acme/webapp#1", "acme/webapp#2"]), "acme");
  assert.equal(plan.open.length, OPEN_ALL_CAP);
  assert.equal(plan.open[0]?.pr.key, "acme/webapp#3");
  assert.equal(plan.remaining, 38 - OPEN_ALL_CAP + 5);
  assert.equal(plan.estimated, true);
});

test("open all under the cap opens everything new and leaves nothing", () => {
  const plan = planOpenAll(
    { total: 2, prs: [hit("webapp", 1), hit("other", 2)] },
    new Set(),
    "acme",
  );
  assert.deepEqual(
    plan.open.map(({ pr }) => pr.key),
    ["acme/webapp#1", "acme/other#2"],
  );
  assert.equal(plan.remaining, 0);
  assert.equal(plan.estimated, false);
});

test("a remainder counted from every result is exact, not an estimate", () => {
  const prs = Array.from({ length: 35 }, (_, i) => hit("webapp", i + 1));
  const plan = planOpenAll({ total: 35, prs }, new Set(["acme/webapp#34"]), "acme");
  assert.equal(plan.remaining, 4);
  assert.equal(plan.estimated, false);
});
