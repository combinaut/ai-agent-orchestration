import assert from "node:assert/strict";
import { test } from "vitest";
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

test("the first ensure asks the host once; later ones reuse the answer", async () => {
  const { ask, sent } = fakeAsk({ panes: { "acme/webapp:feature": { state: "idle" } } });
  const { panes } = panesWith(ask);

  await panes.ensure([]);
  await panes.ensure([HEAD]);
  await panes.ensure([HEAD, { repo: "acme/api", branch: "x" }]);

  assert.deepEqual(sent, [{ type: "panes", heads: [HEAD] }]);
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
