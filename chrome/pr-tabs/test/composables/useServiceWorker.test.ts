import assert from "node:assert/strict";
import { test } from "vitest";
import { useServiceWorker } from "../../src/composables/useServiceWorker";
import { NO_ANSWER, type Request } from "../../src/messages";

test("ask sends through the given sender and retries a lost answer once", async () => {
  const sent: Request[] = [];
  const replies = [undefined, { prs: 1, groups: [] }];
  const { ask } = useServiceWorker(async (message) => {
    sent.push(message);
    return replies[sent.length - 1];
  });
  assert.deepEqual(await ask({ type: "summary" }), { prs: 1, groups: [] });
  assert.deepEqual(sent, [{ type: "summary" }, { type: "summary" }]);
});

test("ask does not retry Open all", async () => {
  let calls = 0;
  const { ask } = useServiceWorker(async () => {
    calls++;
    return undefined;
  });
  assert.deepEqual(await ask({ type: "openAll" }), { error: NO_ANSWER });
  assert.equal(calls, 1);
});

test("by default ask goes through chrome.runtime.sendMessage", async () => {
  const sent: unknown[] = [];
  const runtime = {
    async sendMessage(message: unknown) {
      sent.push(message);
      return { closed: 0 };
    },
  };
  globalThis.chrome = { runtime } as unknown as typeof chrome;
  const { ask } = useServiceWorker();
  assert.deepEqual(await ask({ type: "closeDuplicates" }), { closed: 0 });
  assert.deepEqual(sent, [{ type: "closeDuplicates" }]);
});
