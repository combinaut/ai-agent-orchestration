// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { flushPromises } from "@vue/test-utils";
import type { Window } from "happy-dom";
import { afterEach, test, vi } from "vitest";
import { FILE_SETTINGS_KEY } from "#/files/settings";

const original = chrome;
type Changed = (changes: Record<string, chrome.storage.StorageChange>, area: string) => void;
const addListener = vi.fn<(listener: Changed) => void>();
const removeListener = vi.fn<(listener: Changed) => void>();
const fixture = readFileSync(join(import.meta.dirname, "fixtures/files-react.html"), "utf8");

async function start(load: Promise<Record<string, unknown>>): Promise<void> {
  vi.resetModules();
  document.body.innerHTML = fixture;
  (window as unknown as Window).happyDOM.setURL(
    "https://github.com/example/project/pull/1/changes",
  );
  globalThis.chrome = {
    ...original,
    storage: {
      sync: { get: () => load },
      onChanged: { addListener, removeListener },
    },
  } as unknown as typeof chrome;
  await import("#/files/content");
}

function leave(persisted = false): void {
  const event = new Event("pagehide");
  Object.defineProperty(event, "persisted", { value: persisted });
  window.dispatchEvent(event);
}

afterEach(() => {
  leave();
  document.body.innerHTML = "";
  globalThis.chrome = original;
  addListener.mockClear();
  removeListener.mockClear();
});

test("content applies sync settings live and releases listeners on exit", async () => {
  await start(
    Promise.resolve({ [FILE_SETTINGS_KEY]: { enabled: true, patterns: ["*.stories.*"] } }),
  );
  assert.equal(document.querySelectorAll("[data-pr-tabs-hidden]").length, 2);
  const changed = addListener.mock.calls[0]?.[0];
  changed?.({ [FILE_SETTINGS_KEY]: { newValue: { enabled: false, patterns: [] } } }, "sync");
  assert.equal(document.querySelectorAll("[data-pr-tabs-hidden]").length, 0);
  changed?.(
    { [FILE_SETTINGS_KEY]: { newValue: { enabled: true, patterns: ["*.stories.*"] } } },
    "sync",
  );
  assert.equal(document.querySelectorAll("[data-pr-tabs-hidden]").length, 2);
  leave();
  assert.equal(removeListener.mock.calls.length, 1);
});

test("a late storage read cannot overwrite a newer settings event", async () => {
  const { promise: pending, resolve } = Promise.withResolvers<Record<string, unknown>>();
  await start(pending);
  const changed = addListener.mock.calls[0]?.[0];
  changed?.(
    { [FILE_SETTINGS_KEY]: { newValue: { enabled: true, patterns: ["*.stories.*"] } } },
    "sync",
  );
  resolve({ [FILE_SETTINGS_KEY]: { enabled: false, patterns: [] } });
  await pending;
  await flushPromises();
  assert.equal(document.querySelectorAll("[data-pr-tabs-hidden]").length, 2);
});

test("back-forward cache keeps live filtering active", async () => {
  await start(
    Promise.resolve({ [FILE_SETTINGS_KEY]: { enabled: true, patterns: ["*.stories.*"] } }),
  );
  leave(true);
  assert.equal(removeListener.mock.calls.length, 0);
  assert.equal(document.querySelectorAll("[data-pr-tabs-hidden]").length, 2);
});
