import assert from "node:assert/strict";
import { test } from "vitest";
import { MAX_PANE_LOOKUPS } from "#/core";
import { usePanes } from "#/composables/usePanes";
import { useStatus } from "#/composables/useStatus";
import type { Ask, Reply, Request } from "#/types";
import { fakeAsk } from "./fakeAsk";

const HEAD = { repo: "acme/webapp", branch: "feature" };

function panesWith(ask: Ask) {
  const status = useStatus();
  let closed = 0;

  function countClose() {
    closed++;
  }

  return { panes: usePanes(ask, status, countClose), status, closes: () => closed };
}

test("ensure reuses known heads and fetches only new heads", async () => {
  const { ask, sent } = fakeAsk({ panes: { "acme/webapp:feature": { state: "idle" } } });
  const { panes } = panesWith(ask);

  await panes.ensure([]);
  await panes.ensure([HEAD]);
  await panes.ensure([HEAD, { repo: "acme/api", branch: "x" }]);

  assert.deepEqual(sent, [
    { type: "panes", heads: [HEAD] },
    { type: "panes", heads: [{ repo: "acme/api", branch: "x" }] },
  ]);
  assert.deepEqual(panes.lookup.value, { "acme/webapp:feature": { state: "idle" } });
  assert.equal(panes.checking.value, false);
});

test("a failed lookup shows the error and leaves the lookup empty", async () => {
  const { ask } = fakeAsk({ panes: { error: "herdr not found on PATH" } });
  const { panes, status } = panesWith(ask);

  await panes.ensure([HEAD]);

  assert.deepEqual(panes.lookup.value, {});
  assert.equal(status.statusText.value, "herdr not found on PATH");
  assert.equal(status.isStatusError.value, true);
});

test("focus sends the head and closes the popup; a failure shows the error and stays", async () => {
  const good = fakeAsk({ focusPane: { pane: "w1:p2" } });
  const done = panesWith(good.ask);

  await done.panes.focus(HEAD);

  assert.deepEqual(good.sent, [{ type: "focusPane", head: HEAD }]);
  assert.equal(done.closes(), 1);

  const bad = panesWith(fakeAsk({ focusPane: { error: "no Herdr pane" } }).ask);

  await bad.panes.focus(HEAD);

  assert.equal(bad.closes(), 0);
  assert.equal(bad.status.statusText.value, "no Herdr pane");
});

test("a second click while a focus is in flight sends nothing", async () => {
  const sent: Request[] = [];
  const reply = Promise.withResolvers<Reply<"focusPane">>();

  function ask<M extends Request>(message: M): Promise<Reply<M["type"]>> {
    sent.push(message);

    return reply.promise as Promise<Reply<M["type"]>>;
  }

  const { panes } = panesWith(ask);
  const first = panes.focus(HEAD);

  await panes.focus(HEAD);
  reply.resolve({ pane: "w1:p2" });
  await first;

  assert.equal(sent.length, 1);
});

test("incremental pane reads are single-flight and capped across overlapping triggers", async () => {
  const held = Promise.withResolvers<void>();
  const started = Promise.withResolvers<void>();
  const fake = fakeAsk({ panes: {} });
  let active = 0;
  let maximum = 0;
  const ask: Ask = async (message) => {
    maximum = Math.max(maximum, ++active);
    started.resolve();
    await held.promise;
    active--;

    return fake.ask(message);
  };
  const { panes } = panesWith(ask);
  const first = panes.ensure([HEAD]);
  await started.promise;
  const heads = Array.from({ length: MAX_PANE_LOOKUPS + 5 }, (_value, index) => ({
    repo: "acme/webapp",
    branch: `b${index}`,
  }));
  const second = panes.ensure([HEAD, ...heads]);
  const third = panes.ensure(heads);
  held.resolve();
  await Promise.all([first, second, third]);
  assert.equal(maximum, 1);
  assert.deepEqual(fake.sent, [
    { type: "panes", heads: [HEAD] },
    { type: "panes", heads: heads.slice(0, MAX_PANE_LOOKUPS - 1) },
  ]);
});
