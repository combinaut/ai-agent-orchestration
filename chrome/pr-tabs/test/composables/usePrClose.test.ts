// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import { usePrClose } from "#/composables/usePrClose";
import { useStatus } from "#/composables/useStatus";
import { fakeAsk } from "./fakeAsk";

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

for (const choice of ["next", "previous", "empty"] as const) {
  test(`closing refreshes without leaving the popup and focuses ${choice}`, async () => {
    document.body.innerHTML =
      '<input id="pr-search"><button data-pr-close-id="1" data-pr-close="acme/webapp#1"></button><button data-pr-close-id="2" data-pr-close="acme/webapp#2"></button><button data-pr-close-id="3" data-pr-close="acme/webapp#3"></button>';
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
