import assert from "node:assert/strict";
import { ref } from "vue";
import { test } from "vitest";
import { useGoTo } from "#/composables/useGoTo";
import { useStatus } from "#/composables/useStatus";
import type { Ask, Reply, Request } from "#/types";
import { fakeAsk } from "./fakeAsk";

function noop() {}

function goTo(ask: Ask, available: boolean, text: string) {
  const status = useStatus();
  let closed = 0;
  const refreshes: string[] = [];
  const picker = { available: ref(available), repo: ref("api") };

  function countClose() {
    closed++;
  }

  async function refresh() {
    refreshes.push(status.statusText.value);
  }

  const flow = useGoTo({ ask, status, picker, refresh }, ref(text), countClose);

  return { ...flow, status, closes: () => closed, refreshes };
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

  assert.equal(flow.status.statusText.value, "Not a acme PR number or URL");
  assert.equal(flow.status.isStatusError.value, true);
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

test("row navigation focuses only the existing PR and reports a closed-tab error", async () => {
  const fake = fakeAsk({ focusPr: { focused: "acme/webapp#7" } });
  const flow = goTo(fake.ask, false, "");
  await flow.focusPr("acme/webapp#7");

  assert.deepEqual(fake.sent, [{ type: "focusPr", key: "acme/webapp#7" }]);
  assert.equal(flow.closes(), 1);
  const gone = goTo(
    fakeAsk({ focusPr: { error: "That PR tab is no longer open." } }).ask,
    false,
    "",
  );
  await gone.focusPr("acme/webapp#7");
  assert.equal(gone.closes(), 0);
  assert.equal(gone.status.isStatusError.value, true);
});

test("a pasted list goes as one request with the one picked repo, and a partial failure stays in the popup", async () => {
  const text = "7, 8\nhttps://github.com/acme/api/pull/9";
  const fake = fakeAsk({ goTo: { error: "Couldn't open: 8" } });
  const flow = goTo(fake.ask, true, text);

  await flow.go();

  assert.deepEqual(fake.sent, [{ type: "goTo", input: text, repo: "api" }]);
  assert.equal(flow.status.statusText.value, "Couldn't open: 8");
  assert.equal(flow.closes(), 0);
});

test("a list that opened tabs before failing refreshes first, then shows the error", async () => {
  const fake = fakeAsk({ goTo: { error: "Couldn't open: foo", tabsOpened: 1 } });
  const flow = goTo(fake.ask, false, "7 foo");

  await flow.go();

  assert.deepEqual(flow.refreshes, [""]);
  assert.equal(flow.status.statusText.value, "Couldn't open: foo");
  assert.equal(flow.status.isStatusError.value, true);
  assert.equal(flow.closes(), 0);
});

test("an error that opened nothing does not refresh", async () => {
  const fake = fakeAsk({ goTo: { error: "Couldn't open: foo, bar", tabsOpened: 0 } });
  const flow = goTo(fake.ask, false, "foo bar");

  await flow.go();

  assert.deepEqual(flow.refreshes, []);
});
