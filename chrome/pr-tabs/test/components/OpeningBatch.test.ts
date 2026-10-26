// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { flushPromises } from "@vue/test-utils";
import { test } from "vitest";
import { summarize } from "#/core";
import type { GroupSummary, HostOk, Rules, SearchReply } from "#/types";
import GroupList from "#popup/components/GroupList.vue";
import { fakeChrome, installFakeChrome, world } from "#test/fakeChrome";
import type { OpeningBatchFixture, PopupWrapper } from "#test/types";
import { fakePanes } from "./fakePanes";
import { mountPopup } from "./mountPopup";

const DEFAULT_RULES: Rules = { org: "", defaultRepo: "", openAll: "" };

installFakeChrome();
globalThis.fetch = (async () => ({ json: async () => DEFAULT_RULES })) as unknown as typeof fetch;
const background = await import("#/background");
const RESPONSE: HostOk<SearchReply> = {
  ok: true,
  total: 2,
  prs: [
    { url: "https://github.com/acme/webapp/pull/7", title: "First review", headRefName: "feature" },
    { url: "https://github.com/acme/webapp/pull/8", title: "Second review", headRefName: "later" },
  ],
};

function beginBatch(): OpeningBatchFixture {
  const held = Promise.withResolvers<void>();
  const firstOpened = Promise.withResolvers<void>();
  const create = fakeChrome.tabs.create;
  let calls = 0;
  world.nativeHandler = async () => RESPONSE;
  fakeChrome.tabs.create = async (properties) => {
    if (++calls === 2) {
      firstOpened.resolve();
      await held.promise;
    }

    const tab = await create(properties);
    tab.groupId = 1;

    return tab;
  };

  function restore(): void {
    fakeChrome.tabs.create = create;
  }

  return {
    pending: background.openReviews(),
    firstOpened: firstOpened.promise,
    release: held.resolve,
    restore,
  };
}

async function liveGroups(): Promise<GroupSummary[]> {
  const entries = await background.prEntries({ org: "acme" });
  const groups = new Map([[1, { title: "webapp", color: "blue" as const }]]);

  return summarize(entries, groups, world.store.meta ?? {}).groups;
}

test("expanding a group before opening finishes looks up the already opened PR head", async () => {
  const batch = beginBatch();
  const panes = fakePanes();
  let wrapper: PopupWrapper<typeof GroupList> | undefined;

  try {
    await batch.firstOpened;
    wrapper = mountPopup(GroupList, { props: { groups: await liveGroups(), panes } });
    await wrapper.find("button[aria-expanded]").trigger("click");
    await flushPromises();

    assert.equal(background.openingProgress().active, true);
    assert.deepEqual(panes.ensured, [[{ repo: "acme/webapp", branch: "feature" }]]);
    assert.match(wrapper.text(), /First review/);
  } finally {
    wrapper?.unmount();
    batch.release();
    await batch.pending;
    batch.restore();
  }
});
