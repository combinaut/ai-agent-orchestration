import assert from "node:assert/strict";
import { afterEach, test } from "vitest";
import { useCollapsed } from "#/composables/useCollapsed";

const original = globalThis.chrome;
let stored: Record<string, unknown> = {};

function withStorage(): void {
  globalThis.chrome = {
    ...original,
    storage: {
      local: {
        get: async (key: string) => (key in stored ? { [key]: stored[key] } : {}),
        set: async (values: Record<string, unknown>) => {
          stored = { ...stored, ...values };
        },
      },
    },
  } as unknown as typeof chrome;
}

afterEach(() => {
  globalThis.chrome = original;
  stored = {};
});

test("collapsed groups load from and save to chrome.storage.local", async () => {
  withStorage();
  stored = { prTabsCollapsed: ["1"] };
  const state = useCollapsed();

  await state.load();
  assert.deepEqual([...state.collapsed.value], ["1"]);

  state.set("2", true);
  state.set("1", false);
  assert.deepEqual([...state.collapsed.value], ["2"]);
  assert.deepEqual(stored["prTabsCollapsed"], ["2"]);

  state.set("2", false);
  assert.deepEqual(stored["prTabsCollapsed"], []);
});

test("nothing stored means every group is open", async () => {
  withStorage();
  const state = useCollapsed();

  await state.load();
  assert.equal(state.collapsed.value.size, 0);
});
