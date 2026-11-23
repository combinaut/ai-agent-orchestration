import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import { installFakeChrome, world } from "./fakeChrome";

installFakeChrome();

const A = "https://github.com/acme/webapp/pull/7";
const B = "https://github.com/acme/webapp/pull/8";
const KEY_A = "acme/webapp#7";
const flush = () => new Promise<void>((resolve) => setImmediate(resolve));

function resetWorld(): void {
  world.tabs = [];
  world.store = {};
  world.session = {};
  world.grouped = [];
  world.createdAlarms = [];
}

async function freshWorker(tag: string) {
  return import(/* @vite-ignore */ `#/background.ts?seen-open-${tag}`);
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  resetWorld();
});

test("an old browser session cannot arm a reused tab id", async () => {
  resetWorld();
  world.tabs = [{ id: 1, url: A, active: false }];
  Object.assign(world.store, { seenOpen: { 1: KEY_A } });
  const worker = await freshWorker("browser-session");
  world.nativeHandler = async () => ({ ok: true, prs: { [KEY_A]: { state: "MERGED" } } });

  const result = await worker.checkNow();

  assert.deepEqual(result.closed, []);
});

test("an observation survives a service-worker restart in the same browser session", async () => {
  resetWorld();
  world.tabs = [{ id: 1, url: A, active: false }];
  const worker = await freshWorker("worker-start");
  world.nativeHandler = async () => ({ ok: true, prs: { [KEY_A]: { state: "OPEN" } } });
  await worker.checkNow();

  const restarted = await freshWorker("worker-restart");
  world.nativeHandler = async () => ({ ok: true, prs: { [KEY_A]: { state: "MERGED" } } });
  const result = await restarted.checkNow();

  assert.equal(result.closed.length, 1);
});

test("leaving and returning to a PR does not restore its old observation", async () => {
  resetWorld();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  world.tabs = [{ id: 1, url: A, active: false }];
  const worker = await freshWorker("navigation");
  world.nativeHandler = async () => ({ ok: true, prs: { [KEY_A]: { state: "OPEN" } } });
  await worker.checkNow();
  await flush();

  let release: ((reply: { ok: true; prs: Record<string, { state: "OPEN" }> }) => void) | undefined;
  world.nativeHandler = () => new Promise((resolve) => (release = resolve));
  const checking = worker.checkNow();
  await flush();

  world.tabs = [{ id: 1, url: B, active: false }];
  world.tabUpdatedListener(1, { url: B }, world.tabs[0] as chrome.tabs.Tab);
  world.tabs = [{ id: 1, url: A, active: false }];
  world.tabUpdatedListener(1, { url: A }, world.tabs[0] as chrome.tabs.Tab);
  assert.ok(release);
  release({ ok: true, prs: { [KEY_A]: { state: "OPEN" } } });
  await checking;
  await vi.advanceTimersByTimeAsync(401);
  await flush();

  world.nativeHandler = async () => ({ ok: true, prs: { [KEY_A]: { state: "MERGED" } } });
  const result = await worker.checkNow();

  assert.deepEqual(result.closed, []);
});

test("a delayed prune cannot erase a newer open observation", async () => {
  resetWorld();
  const worker = await freshWorker("prune");
  let release: ((tabs: chrome.tabs.Tab[]) => void) | undefined;
  const query = vi
    .spyOn(chrome.tabs, "query")
    .mockImplementationOnce(() => new Promise((resolve) => (release = resolve)));
  world.tabRemovedListener(999, { isWindowClosing: false, windowId: 1 });
  await flush();

  world.tabs = [{ id: 1, url: A, active: false }];
  world.nativeHandler = async () => ({ ok: true, prs: { [KEY_A]: { state: "OPEN" } } });
  await worker.checkNow();
  await flush();

  assert.ok(release);
  release([]);
  await flush();
  await flush();
  world.nativeHandler = async () => ({ ok: true, prs: { [KEY_A]: { state: "MERGED" } } });
  const result = await worker.checkNow();

  assert.equal(result.closed.length, 1);
  query.mockRestore();
});

test("metadata hydration records an open observation for the current tab", async () => {
  resetWorld();
  world.tabs = [{ id: 1, url: A, active: false }];
  const worker = await freshWorker("hydration");
  world.nativeHandler = async () => ({
    ok: true,
    prs: { [KEY_A]: { state: "OPEN", title: "t", headRefName: "b" } },
  });

  await worker.summary();
  await flush();
  await flush();
  world.nativeHandler = async () => ({ ok: true, prs: { [KEY_A]: { state: "MERGED" } } });
  const result = await worker.checkNow();

  assert.equal(result.closed.length, 1);
});
