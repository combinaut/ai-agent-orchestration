// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises } from "@vue/test-utils";
import type { TabId } from "#/types";
import { afterEach, test, vi } from "vitest";
import { usePrClose } from "#/composables/usePrClose";
import { useStatus } from "#/composables/useStatus";
import { fakeAsk } from "./fakeAsk";
import type { CloseQueueFixture } from "#test/types";

const ROWS =
  '<input id="pr-search"><button data-pr-close-id="1" data-pr-close="acme/webapp#1"></button><button data-pr-close-id="2" data-pr-close="acme/webapp#2"></button><button data-pr-close-id="3" data-pr-close="acme/webapp#3"></button>';

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

for (const choice of ["next", "previous", "empty"] as const) {
  test(`closing refreshes without leaving the popup and focuses ${choice}`, async () => {
    document.body.innerHTML = ROWS;
    const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>("button"));
    const tabId = choice === "previous" ? 3 : 2;
    const key = choice === "previous" ? "acme/webapp#3" : "acme/webapp#2";
    buttons.find((button) => button.dataset.prClose === key)?.focus();
    const fake = fakeAsk({ closePr: { closed: 1 } });
    const closeWindow = vi.spyOn(window, "close");
    async function refresh(): Promise<void> {
      if (choice === "empty") {
        document.querySelectorAll("button").forEach((button) => button.remove());
      } else {
        document.querySelector(`button[data-pr-close="${key}"]`)?.remove();
      }
    }
    const flow = usePrClose({ ask: fake.ask, status: useStatus(), refresh });
    await flow.close(key, tabId);
    const expected = choice === "next" ? "acme/webapp#3" : "acme/webapp#2";
    assert.deepEqual(fake.sent, [{ type: "closePr", key, tabId }]);
    assert.equal(closeWindow.mock.calls.length, 0);
    assert.equal(
      choice === "empty"
        ? document.activeElement?.id
        : (document.activeElement as HTMLElement).dataset.prClose,
      choice === "empty" ? "pr-search" : expected,
    );
  });
}

function closeQueueFixture(holdRefresh = false): CloseQueueFixture {
  document.body.innerHTML = ROWS;
  const held = Promise.withResolvers<void>();
  const tabs = new Set<TabId>([1, 2, 3]);
  const fake = fakeAsk({ closePr: { closed: 1 } });
  const ask: typeof fake.ask = async (message) => {
    const reply = await fake.ask(message);
    if (!holdRefresh) {
      await held.promise;
    }

    if (message.type === "closePr") {
      tabs.delete(message.tabId);
    }

    return reply;
  };

  async function refresh(): Promise<void> {
    if (holdRefresh) {
      await held.promise;
    }

    for (const button of document.querySelectorAll<HTMLButtonElement>("button[data-pr-close-id]")) {
      if (!tabs.has(Number(button.dataset.prCloseId))) {
        button.remove();
      }
    }
  }

  return { flow: usePrClose({ ask, status: useStatus(), refresh }), held, tabs, sent: fake.sent };
}

for (const holdRefresh of [false, true]) {
  test(`quick distinct closes queue through ${holdRefresh ? "refresh" : "ask"}, deduplicate keys and preserve focus`, async () => {
    const fixture = closeQueueFixture(holdRefresh);
    const first = fixture.flow.close("acme/webapp#1", 1);
    try {
      const second = fixture.flow.close("acme/webapp#2", 2);
      const duplicateFirst = fixture.flow.close("acme/webapp#1", 1);
      const duplicateSecond = fixture.flow.close("acme/webapp#2", 2);
      await flushPromises();
      assert.equal(fixture.sent.length, 1);
      fixture.held.resolve();
      await Promise.all([first, second, duplicateFirst, duplicateSecond]);
      assert.deepEqual([...fixture.tabs], [3]);
      assert.deepEqual(fixture.sent, [
        { type: "closePr", key: "acme/webapp#1", tabId: 1 },
        { type: "closePr", key: "acme/webapp#2", tabId: 2 },
      ]);
      assert.equal((document.activeElement as HTMLElement).dataset.prCloseId, "3");
    } finally {
      fixture.held.resolve();
      await first;
    }
  });
}

test("pending keys deduplicate changed row IDs and become available after settling", async () => {
  const fixture = closeQueueFixture();
  const first = fixture.flow.close("acme/webapp#1", 1);
  const duplicate = fixture.flow.close("acme/webapp#1", 999);
  fixture.held.resolve();
  await Promise.all([first, duplicate]);
  assert.equal(fixture.sent.length, 1);

  fixture.tabs.add(4);
  document.body.insertAdjacentHTML(
    "beforeend",
    '<button data-pr-close-id="4" data-pr-close="acme/webapp#1"></button>',
  );
  await fixture.flow.close("acme/webapp#1", 4);
  assert.equal(fixture.tabs.has(4), false);
  assert.deepEqual(fixture.sent.at(-1), { type: "closePr", key: "acme/webapp#1", tabId: 4 });
});
