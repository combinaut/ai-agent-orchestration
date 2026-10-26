// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { afterEach, beforeEach, test, vi } from "vitest";
import { flushPromises } from "@vue/test-utils";
import type * as Background from "#/background";
import { MAX_PRS_PER_POLL } from "#/core";
import { usePanes } from "#/composables/usePanes";
import { useStatus } from "#/composables/useStatus";
import type { HostRequest, HostOk, StatesReply, Request, Reply } from "#/types";
import { fakeChrome, installFakeChrome, world } from "#test/fakeChrome";
import GroupList from "#popup/components/GroupList.vue";
import { mountPopup } from "./mountPopup";

installFakeChrome();
const URL = "https://github.com/acme/webapp/pull/7";
const RESPONSE: HostOk<StatesReply> = {
  ok: true,
  owners: { "acme/webapp:feature": "Finder" },
  prs: { "acme/webapp#7": { state: "OPEN", title: "Restored title", headRefName: "feature" } },
};
let version = 0;

function freshWorker(): Promise<typeof Background> {
  return import(/* @vite-ignore */ `#/background.ts?backfill-${++version}`);
}

beforeEach(() => {
  vi.stubGlobal("fetch", async () => ({
    json: async () => ({ org: "", defaultRepo: "", openAll: "" }),
  }));
  world.tabs = [{ id: 1, url: URL, windowId: 1, groupId: 1 }];
  world.store = {};
  world.grouped = [];
  world.nativeHandler = async (message) =>
    message.type === "panes"
      ? { ok: true, panes: { "acme/webapp:feature": { state: "working" } } }
      : RESPONSE;
  vi.spyOn(fakeChrome.tabGroups, "get").mockImplementation(async () => ({
    id: 1,
    title: world.grouped.at(-1)?.title ?? "webapp",
    color: "blue",
    collapsed: false,
    windowId: 1,
  }));
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test("empty storage restores open PR titles, heads, architect groups and pane controls", async () => {
  const worker = await freshWorker();
  await worker.summary();
  await flushPromises();
  const summary = await worker.summary();
  assert.equal(summary.groups[0]?.title, "Finder");
  assert.equal(summary.groups[0]?.prs[0]?.title, "Restored title");
  assert.deepEqual(summary.groups[0]?.prs[0]?.head, { repo: "acme/webapp", branch: "feature" });
  assert.equal(world.store.meta?.["acme/webapp#7"]?.title, "Restored title");
  function ask<M extends Request>(message: M): Promise<Reply<M["type"]>> {
    assert.equal(message.type, "panes");

    return worker.panes("heads" in message ? message.heads : []) as Promise<Reply<M["type"]>>;
  }
  const panes = usePanes(ask, useStatus());
  const wrapper = mountPopup(GroupList, { props: { groups: summary.groups, panes } });
  try {
    await wrapper.find("button[aria-expanded]").trigger("click");
    await flushPromises();
    assert.equal(wrapper.findAll("li [data-pr-pane]").length, 1);
    assert.ok(wrapper.text().includes("Restored title"));
  } finally {
    wrapper.unmount();
  }
});

test("install, startup and parallel summaries share one metadata fetch", async () => {
  const worker = await freshWorker();
  const held = Promise.withResolvers<void>();
  let calls = 0;
  world.nativeHandler = async () => {
    calls++;
    await held.promise;

    return RESPONSE;
  };
  const summaries = [worker.summary(), worker.summary()];
  const installing = world.installedListener();
  const starting = world.startupListener();
  try {
    await flushPromises();
    assert.equal(calls, 1);
    held.resolve();
    await Promise.all([...summaries, installing, starting]);
    await worker.summary();
    assert.equal(calls, 1);
  } finally {
    held.resolve();
    await Promise.all([...summaries, installing, starting]);
  }
});

test("metadata hydration caps unique PRs and does not close merged tabs", async () => {
  world.tabs = Array.from({ length: MAX_PRS_PER_POLL + 4 }, (_value, index) => ({
    id: index + 1,
    url: URL.replace("/7", `/${index + 1}`),
  }));
  const first = world.tabs[0];
  assert.ok(first);
  world.tabs.push({ id: 999, url: first.url });
  const messages: HostRequest[] = [];
  world.nativeHandler = async (message) => {
    messages.push(message);

    return {
      ...RESPONSE,
      prs: { "acme/webapp#7": { state: "MERGED", title: "Merged", headRefName: "feature" } },
    };
  };
  const worker = await freshWorker();
  await worker.summary();
  assert.equal(messages.length, 1);
  assert.equal(messages[0]?.type === "states" ? messages[0].prs.length : 0, MAX_PRS_PER_POLL);
  assert.equal(world.tabs.length, MAX_PRS_PER_POLL + 5);
});

test("timed-out hydration preserves cached data, cools down and can retry", async () => {
  vi.useFakeTimers();
  vi.spyOn(console, "warn").mockImplementation(() => {});
  world.store.meta = { "acme/webapp#7": { title: "Cached title" } };
  const held = Promise.withResolvers<HostOk<StatesReply>>();
  let requests = 0;
  world.nativeHandler = async () => (++requests === 1 ? held.promise : RESPONSE);
  const worker = await freshWorker();
  try {
    const first = worker.summary();
    await flushPromises();
    await vi.advanceTimersByTimeAsync(45001);
    assert.equal((await first).groups[0]?.prs[0]?.title, "Cached title");
    assert.equal(requests, 1);
    await worker.summary();
    assert.equal(requests, 1);
    await vi.advanceTimersByTimeAsync(15000);
    await worker.summary();
    await flushPromises();
    const restored = await worker.summary();
    assert.equal(requests, 2);
    assert.equal(restored.groups[0]?.prs[0]?.title, "Restored title");
  } finally {
    held.resolve(RESPONSE);
  }
});

test("hydration merges current metadata and rechecks tabs before regrouping", async () => {
  const held = Promise.withResolvers<HostOk<StatesReply>>();
  const worker = await freshWorker();
  world.nativeHandler = async () => {
    return held.promise;
  };
  const summary = worker.summary();
  try {
    await flushPromises();
    assert.equal(world.grouped.length, 0);
    world.store.meta = { "acme/webapp#99": { title: "Concurrent new PR", headRefName: "new" } };
    const tab = world.tabs[0];
    assert.ok(tab);
    tab.url = "https://example.com";
    held.resolve(RESPONSE);
    await summary;
    await flushPromises();
    assert.equal((await worker.summary()).prs, 0);
    assert.equal(world.store.meta?.["acme/webapp#99"]?.title, "Concurrent new PR");
    assert.equal(world.grouped.length, 0);
  } finally {
    held.resolve(RESPONSE);
    await summary;
  }
});

test("a held hydration never delays cached summary or startup grouping", async () => {
  const held = Promise.withResolvers<HostOk<StatesReply>>();
  world.store.meta = { "acme/webapp#7": { title: "Cached title" } };
  world.nativeHandler = async () => held.promise;
  const worker = await freshWorker();
  let returned = false;
  const reading = worker.summary().then((value) => {
    returned = true;

    return value;
  });
  const starting = world.startupListener();
  try {
    await flushPromises();
    assert.equal(returned, true);
    assert.equal(world.grouped.at(-1)?.title, "webapp");
    assert.equal((await reading).groups[0]?.prs[0]?.title, "Cached title");
  } finally {
    held.resolve(RESPONSE);
    await Promise.all([reading, starting]);
  }
});

test("parallel triggers keep hydration active until the shared request settles", async () => {
  const held = Promise.withResolvers<HostOk<StatesReply>>();
  let requests = 0;
  world.nativeHandler = async () => {
    requests++;

    return held.promise;
  };
  const worker = await freshWorker();
  const first = worker.summary();
  try {
    await flushPromises();
    world.tabs.push({ id: 2, url: URL.replace("/7", "/8"), windowId: 1, groupId: 1 });
    const second = worker.summary();
    const starting = world.startupListener();
    await flushPromises();
    assert.equal(requests, 1);
    assert.equal(worker.openingProgress().hydrating, true);
    held.resolve(RESPONSE);
    await Promise.all([first, second, starting]);
    await flushPromises();
    assert.equal(worker.openingProgress().hydrating, undefined);
    assert.equal(worker.openingProgress().metadataRevision, 1);
  } finally {
    held.resolve(RESPONSE);
    await first;
  }
});

test("startup alone hydrates metadata and restores the architect group", async () => {
  await freshWorker();
  let requests = 0;
  world.nativeHandler = async () => {
    requests++;

    return RESPONSE;
  };
  await world.startupListener();
  await flushPromises();
  assert.equal(requests, 1);
  assert.equal(world.grouped.at(-1)?.title, "Finder");
  assert.equal(world.store.meta?.["acme/webapp#7"]?.title, "Restored title");
});
