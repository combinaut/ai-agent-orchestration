import assert from "node:assert/strict";
import { test } from "vitest";
import { useServiceWorker } from "#/composables/useServiceWorker";

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
