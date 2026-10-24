import assert from "node:assert/strict";
import { ref } from "vue";
import { test } from "vitest";
import { useGoTo } from "#/composables/useGoTo";
import { useStatus } from "#/composables/useStatus";
import type { Ask } from "#/composables/useServiceWorker";
import type { Reply, Request } from "#/messages";
import { fakeAsk } from "./fakeAsk";

function noop() {}

function goTo(ask: Ask, available: boolean, text: string) {
  const status = useStatus();
  let closed = 0;
  const picker = { available: ref(available), repo: ref("api") };

  function countClose() {
    closed++;
  }

  const flow = useGoTo({ ask, status, picker }, ref(text), countClose);

  return { ...flow, status, closes: () => closed };
}

test("blank input sends nothing", async () => {
  const { ask, sent } = fakeAsk({ goTo: { opened: "acme/webapp#1" } });
  const flow = goTo(ask, true, "  ");

  await flow.go();

  assert.deepEqual(sent, []);
  assert.equal(flow.closes(), 0);
});

test("the picked repo goes along only while the dropdown is available, and success closes the popup", async () => {
  const withPicker = fakeAsk({ goTo: { opened: "acme/api#7" } });
  const picked = goTo(withPicker.ask, true, "7");

  await picked.go();

  assert.deepEqual(withPicker.sent, [{ type: "goTo", input: "7", repo: "api" }]);
  assert.equal(picked.closes(), 1);

  const without = fakeAsk({ goTo: { opened: "acme/webapp#7" } });

  await goTo(without.ask, false, "7").go();

  assert.deepEqual(without.sent, [{ type: "goTo", input: "7" }]);
});

test("an error stays in the popup", async () => {
  const flow = goTo(fakeAsk({ goTo: { error: "Not a acme PR number or URL" } }).ask, false, "x");

  await flow.go();

  assert.deepEqual(flow.status.status.value, {
    kind: "error",
    text: "Not a acme PR number or URL",
  });
  assert.equal(flow.closes(), 0);
  assert.equal(flow.busy.value, false);
});

test("a second Enter while one request runs sends nothing more", async () => {
  let release: () => void = noop;
  const sent: Request[] = [];

  function waitForRelease(resolve: () => void) {
    release = resolve;
  }

  async function ask<M extends Request>(message: M): Promise<Reply<M["type"]>> {
    sent.push(message);
    await new Promise<void>(waitForRelease);
    return { error: "released" };
  }

  const flow = goTo(ask, false, "7");
  const first = flow.go();

  assert.equal(flow.busy.value, true);

  await flow.go();
  release();
  await first;

  assert.equal(sent.length, 1);
});
