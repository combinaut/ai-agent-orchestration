import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import type * as Background from "#/background";
import type { Closed, HostRequest, PrState } from "#/types";
import { fakeChrome, installFakeChrome, world } from "./fakeChrome";

installFakeChrome();

const A = "https://github.com/acme/webapp/pull/7";
const B = "https://github.com/acme/webapp/pull/8";
const KEY_A = "acme/webapp#7";
const KEY_B = "acme/webapp#8";
const realNow = Date.now;
const flush = () => new Promise<void>((resolve) => setImmediate(resolve));

let now = realNow();

function tick(): string {
  now += 1;

  return new Date(now).toISOString();
}

function resetWorld(): void {
  now = realNow();
  Date.now = () => now;
  world.tabs = [];
  world.store = {};
  world.session = {};
  world.grouped = [];
  world.createdAlarms = [];
}

function freshWorker(tag: string): Promise<typeof Background> {
  return import(/* @vite-ignore */ `#/background.ts?tab-visits-${tag}`);
}

function answer(prs: Record<string, PrState>) {
  return async () => ({ ok: true, prs });
}

function navigate(id: number, url: string): void {
  const tab = { id, url, active: false };

  world.tabs = [...world.tabs.filter((candidate) => candidate.id !== id), tab];
  world.tabUpdatedListener(id, { url }, tab as chrome.tabs.Tab);
}

// Answers with prs and holds the close's visit read, the first session read after the reply.
function answerHoldingCloseRead(prs: Record<string, PrState>) {
  const hold: { release?: () => void } = {};

  world.nativeHandler = async () => {
    const { session } = fakeChrome.storage;
    const read = session.get.bind(session);
    let reads = 0;

    vi.spyOn(session, "get").mockImplementation((key) => {
      const snapshot = read(key);
      reads += 1;

      return reads === 1
        ? new Promise((resolve) => (hold.release = () => resolve(snapshot)))
        : snapshot;
    });

    return { ok: true, prs };
  };

  return hold;
}

async function closedBy(worker: typeof Background): Promise<Closed[]> {
  const result = await worker.checkNow();

  assert.ok("closed" in result);

  return result.closed;
}

afterEach(async () => {
  await flush();
  vi.restoreAllMocks();
  vi.useRealTimers();
  resetWorld();
  Date.now = realNow;
});

test("a subpage of the same PR keeps the visit after a service-worker restart", async () => {
  resetWorld();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  world.tabs = [{ id: 1, url: A, active: false }];
  const worker = await freshWorker("subpage-before");
  world.nativeHandler = answer({ [KEY_A]: { state: "OPEN" } });
  await worker.checkNow();
  await flush();

  const restarted = await freshWorker("subpage-after");
  const closedAt = tick();
  tick();
  navigate(1, `${A}/files`);
  await flush();
  world.nativeHandler = answer({ [KEY_A]: { state: "MERGED", closedAt } });

  assert.equal((await closedBy(restarted)).length, 1);
});

test("leaving and returning to a PR starts a new visit, even during the final tab read", async () => {
  resetWorld();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  world.tabs = [{ id: 1, url: A, active: false }];
  const worker = await freshWorker("close-gap");
  world.nativeHandler = answer({ [KEY_A]: { state: "OPEN" } });
  await worker.checkNow();
  await flush();

  let release: ((tab: chrome.tabs.Tab) => void) | undefined;
  vi.spyOn(chrome.tabs, "get").mockImplementationOnce(
    () => new Promise((resolve) => (release = resolve)),
  );
  const closedAt = tick();
  world.nativeHandler = answer({ [KEY_A]: { state: "MERGED", closedAt } });
  const checking = closedBy(worker);
  await flush();
  assert.ok(release);

  tick();
  navigate(1, B);
  navigate(1, A);
  release(world.tabs[0] as chrome.tabs.Tab);

  assert.deepEqual(await checking, []);
  await vi.advanceTimersByTimeAsync(401);
  world.nativeHandler = answer({ [KEY_A]: { state: "MERGED", closedAt } });
  assert.deepEqual(await closedBy(worker), []);
});

test("leaving and returning to a PR during the final visit read keeps the tab", async () => {
  resetWorld();
  world.tabs = [{ id: 1, url: A, active: false }];
  const worker = await freshWorker("visit-read-gap");
  world.nativeHandler = answer({ [KEY_A]: { state: "OPEN" } });
  await worker.checkNow();
  await flush();

  const hold = answerHoldingCloseRead({ [KEY_A]: { state: "MERGED", closedAt: tick() } });
  const checking = closedBy(worker);
  await vi.waitFor(() => assert.ok(hold.release));

  tick();
  navigate(1, B);
  navigate(1, A);
  hold.release?.();

  assert.deepEqual(await checking, []);
});

test("a subpage of the same PR opened during the final visit read still closes", async () => {
  resetWorld();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  const worker = await freshWorker("subpage-gap");
  navigate(1, A);
  world.nativeHandler = answer({ [KEY_A]: { state: "OPEN" } });
  await worker.checkNow();
  await flush();

  const hold = answerHoldingCloseRead({ [KEY_A]: { state: "MERGED", closedAt: tick() } });
  const checking = closedBy(worker);
  await vi.waitFor(() => assert.ok(hold.release));

  navigate(1, `${A}/files`);
  hold.release?.();

  assert.deepEqual(await checking, [{ key: KEY_A, state: "MERGED" }]);
});

test("a visit write still pending when the close reads visits keeps the returned tab", async () => {
  resetWorld();
  world.tabs = [{ id: 1, url: A, active: false }];
  const worker = await freshWorker("pending-write");
  world.nativeHandler = answer({ [KEY_A]: { state: "OPEN" } });
  await worker.checkNow();
  await flush();

  const closedAt = tick();
  let reply: ((value: unknown) => void) | undefined;
  world.nativeHandler = () => new Promise((resolve) => (reply = resolve));
  const checking = closedBy(worker);
  await vi.waitFor(() => assert.ok(reply));

  const { session } = fakeChrome.storage;
  const write = session.set.bind(session);
  let persist: (() => void) | undefined;
  vi.spyOn(session, "set").mockImplementationOnce(
    (values) => new Promise((resolve) => (persist = () => resolve(write(values)))),
  );
  tick();
  navigate(1, B);
  navigate(1, A);
  reply?.({ ok: true, prs: { [KEY_A]: { state: "MERGED", closedAt } } });
  await flush();
  persist?.();

  assert.deepEqual(await checking, []);
});

test("a tab activated during the final visit read stays", async () => {
  resetWorld();
  world.tabs = [{ id: 1, url: A, active: false }];
  const worker = await freshWorker("activation-gap");
  world.nativeHandler = answer({ [KEY_A]: { state: "OPEN" } });
  await worker.checkNow();
  await flush();

  const hold = answerHoldingCloseRead({ [KEY_A]: { state: "MERGED", closedAt: tick() } });
  const checking = closedBy(worker);
  await vi.waitFor(() => assert.ok(hold.release));

  const [tab] = world.tabs;
  assert.ok(tab);
  tab.active = true;
  hold.release?.();

  assert.deepEqual(await checking, []);
});

test("a scan from before the tab left PRs does not restore its visit", async () => {
  resetWorld();
  world.tabs = [{ id: 1, url: A, active: false }];
  const worker = await freshWorker("stale-scan");
  world.nativeHandler = answer({ [KEY_A]: { state: "OPEN" } });
  await worker.checkNow();
  await flush();

  const scanned = world.tabs.map((tab) => ({ ...tab }));
  let release: ((tabs: chrome.tabs.Tab[]) => void) | undefined;
  vi.spyOn(chrome.tabs, "query").mockImplementationOnce(
    () => new Promise((resolve) => (release = resolve)),
  );
  const scanning = worker.prEntries({ org: "acme" });
  await flush();

  tick();
  navigate(1, "https://github.com/acme/webapp/pulls");
  await flush();
  release?.(scanned as chrome.tabs.Tab[]);
  await scanning;

  const closedAt = tick();
  tick();
  navigate(1, A);
  await flush();
  world.nativeHandler = answer({ [KEY_A]: { state: "MERGED", closedAt } });

  assert.deepEqual(await closedBy(worker), []);
});

test("a same-PR subpage opened during a scan after a worker restart keeps the visit", async () => {
  resetWorld();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  world.tabs = [{ id: 1, url: A, active: false }];
  const worker = await freshWorker("scan-subpage-before");
  world.nativeHandler = answer({ [KEY_A]: { state: "OPEN" } });
  await worker.checkNow();
  await flush();

  const restarted = await freshWorker("scan-subpage-after");
  const scanned = world.tabs.map((tab) => ({ ...tab }));
  let release: ((tabs: chrome.tabs.Tab[]) => void) | undefined;
  vi.spyOn(chrome.tabs, "query").mockImplementationOnce(
    () => new Promise((resolve) => (release = resolve)),
  );
  tick();
  const scanning = restarted.prEntries({ org: "acme" });
  await flush();

  tick();
  navigate(1, `${A}/files`);
  await flush();
  release?.(scanned as chrome.tabs.Tab[]);
  await scanning;

  const closedAt = tick();
  tick();
  world.nativeHandler = answer({ [KEY_A]: { state: "MERGED", closedAt } });

  assert.deepEqual(await closedBy(restarted), [{ key: KEY_A, state: "MERGED" }]);
});

test("a delayed prune cannot drop a newer visit", async () => {
  resetWorld();
  const worker = await freshWorker("prune");
  let release: ((tabs: chrome.tabs.Tab[]) => void) | undefined;
  vi.spyOn(chrome.tabs, "query").mockImplementationOnce(
    () => new Promise((resolve) => (release = resolve)),
  );
  world.tabRemovedListener(999, { isWindowClosing: false, windowId: 1 });
  await vi.waitFor(() => assert.ok(release));

  tick();
  world.tabs = [{ id: 1, url: A, active: false }];
  world.nativeHandler = answer({ [KEY_A]: { state: "OPEN" } });
  const checking = worker.checkNow();
  await flush();

  const closedAt = tick();
  tick();
  release?.([]);
  await checking;
  world.nativeHandler = answer({ [KEY_A]: { state: "MERGED", closedAt } });

  assert.equal((await closedBy(worker)).length, 1);
});

test("an older poll keeps the visit of a tab opened during its host request", async () => {
  resetWorld();
  world.tabs = [{ id: 1, url: A, active: false }];
  const worker = await freshWorker("overlap");
  let release: ((reply: unknown) => void) | undefined;
  world.nativeHandler = () => new Promise((resolve) => (release = resolve));
  const checking = worker.checkNow();
  await flush();
  assert.ok(release);

  tick();
  world.tabs.push({ id: 2, url: B, active: false });
  world.nativeHandler = answer({
    [KEY_A]: { state: "OPEN", title: "a", headRefName: "a" },
    [KEY_B]: { state: "OPEN", title: "b", headRefName: "b" },
  });
  await worker.summary();
  await flush();
  await flush();
  const visit = world.session.tabVisits?.[2];

  tick();
  release({ ok: true, prs: { [KEY_A]: { state: "OPEN" } } });
  await checking;
  await flush();

  assert.deepEqual(world.session.tabVisits?.[2], visit);
  world.nativeHandler = answer({ [KEY_B]: { state: "MERGED", closedAt: tick() } });
  assert.deepEqual(await closedBy(worker), [{ key: KEY_B, state: "MERGED" }]);
});

test("tabs outside the polled batch keep their visits until their PR is checked", async () => {
  resetWorld();
  world.tabs = Array.from({ length: 101 }, (_, i) => ({
    id: i + 1,
    url: `https://github.com/acme/webapp/pull/${i + 1}`,
    active: false,
  }));
  const worker = await freshWorker("batches");
  let closedAt: string | undefined;
  world.nativeHandler = async (request: HostRequest) => {
    assert.equal(request.type, "states");

    return {
      ok: true,
      prs: Object.fromEntries(
        request.prs.map((pr) => [
          `acme/webapp#${pr.number}`,
          closedAt ? { state: "MERGED", closedAt } : { state: "OPEN" },
        ]),
      ),
    };
  };

  await worker.checkNow();
  closedAt = tick();

  assert.equal((await closedBy(worker)).length, 100);
  assert.equal((await closedBy(worker)).length, 1);
});

test("the tab in view stays and is reported until the user leaves it", async () => {
  resetWorld();
  world.tabs = [{ id: 1, url: A, active: true }];
  const worker = await freshWorker("in-view");
  world.nativeHandler = answer({ [KEY_A]: { state: "OPEN" } });
  await worker.checkNow();
  world.nativeHandler = answer({ [KEY_A]: { state: "MERGED", closedAt: tick() } });

  assert.deepEqual(await worker.checkNow(), {
    tabs: 1,
    prs: 1,
    checked: 1,
    closed: [],
    inView: [KEY_A],
  });

  const [tab] = world.tabs;
  assert.ok(tab);
  tab.active = false;
  assert.equal((await closedBy(worker)).length, 1);
});
