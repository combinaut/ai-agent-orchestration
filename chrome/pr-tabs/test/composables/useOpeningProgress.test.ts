// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { effectScope, ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { test, vi } from "vitest";
import type { SummaryList } from "#/types";
import type { FakeReplies } from "#test/types";
import { useStatus } from "#/composables/useStatus";
import { useSummary } from "#/composables/useSummary";
import { useOpeningProgress } from "#/composables/useOpeningProgress";
import { fakeAsk } from "./fakeAsk";

test("popup reads ongoing progress and stops polling when its scope closes", async () => {
  vi.useFakeTimers();
  const scope = effectScope();
  const fake = fakeAsk({ openingProgress: { active: true, opened: 3, planned: 10 } });

  try {
    const progress = scope.run(() =>
      useOpeningProgress({
        ask: fake.ask,
        status: useStatus(),
        refresh: async () => {},
        localBusy: ref(false),
      }),
    );
    await flushPromises();

    assert.deepEqual(progress?.value, { active: true, opened: 3, planned: 10 });
    await vi.advanceTimersByTimeAsync(250);
    assert.equal(fake.sent.length, 2);
    scope.stop();
    await vi.advanceTimersByTimeAsync(500);
    assert.equal(fake.sent.length, 2);
  } finally {
    scope.stop();
    vi.useRealTimers();
  }
});

test("an observed local opening keeps its own completion report", async () => {
  vi.useFakeTimers();
  const scope = effectScope();
  const replies: FakeReplies = { openingProgress: { active: true, opened: 1, planned: 3 } };
  const fake = fakeAsk(replies);
  const status = useStatus();
  const localBusy = ref(true);
  const refresh = vi.fn<SummaryList["refresh"]>(async () => {});

  try {
    scope.run(() =>
      useOpeningProgress({
        ask: fake.ask,
        status,
        localBusy,
        refresh,
      }),
    );
    await flushPromises();
    localBusy.value = false;
    status.info("Local action completed");
    const result = { opened: 3, remaining: 0, estimated: false };
    replies.openingProgress = { active: false, opened: 0, planned: 0, result };
    await vi.advanceTimersByTimeAsync(250);

    assert.equal(refresh.mock.calls.length, 1);
    assert.equal(status.statusText.value, "Local action completed");
  } finally {
    scope.stop();
    vi.useRealTimers();
  }
});

test("an observed completion cannot overwrite a newer local error while refreshing", async () => {
  vi.useFakeTimers();
  const scope = effectScope();
  const held = Promise.withResolvers<void>();
  const replies: FakeReplies = { openingProgress: { active: true, opened: 0, planned: 3 } };
  const fake = fakeAsk(replies);
  const status = useStatus();
  const refresh = vi.fn<SummaryList["refresh"]>(() => held.promise);

  try {
    scope.run(() => useOpeningProgress({ ask: fake.ask, status, localBusy: ref(false), refresh }));
    await flushPromises();
    const result = { opened: 3, remaining: 0, estimated: false };
    replies.openingProgress = { active: false, opened: 0, planned: 0, result };
    await vi.advanceTimersByTimeAsync(250);
    assert.equal(refresh.mock.calls.length, 1);
    status.error("newer local failure");
    held.resolve();
    await flushPromises();

    assert.equal(status.statusText.value, "newer local failure");
    assert.equal(status.isStatusError.value, true);
  } finally {
    held.resolve();
    scope.stop();
    vi.useRealTimers();
  }
});

for (const stage of ["ask", "refresh"] as const) {
  test(`progress reads stay single-flight while ${stage} is held`, async () => {
    vi.useFakeTimers();
    const scope = effectScope();
    const held = Promise.withResolvers<void>();
    const base = fakeAsk({ openingProgress: { active: true, opened: 1, planned: 3 } });
    let reads = 0;
    const askHold = stage === "ask" ? held.promise : Promise.resolve();
    const refreshHold = stage === "refresh" ? held.promise : Promise.resolve();
    const ask: typeof base.ask = async (message) => {
      reads++;
      await askHold;

      return base.ask(message);
    };
    const refresh = (): Promise<void> => refreshHold;
    const context = { ask, refresh, status: useStatus(), localBusy: ref(false) };

    try {
      scope.run(() => useOpeningProgress(context));
      await vi.advanceTimersByTimeAsync(1000);
      assert.equal(reads, 1);
      held.resolve();
      await flushPromises();
    } finally {
      held.resolve();
      scope.stop();
      vi.useRealTimers();
    }
  });
}

test("metadata hydration finishing after a failure keeps the failure message", async () => {
  vi.useFakeTimers();
  const scope = effectScope();
  const replies: FakeReplies = {
    openingProgress: { active: false, opened: 0, planned: 0, metadataRevision: 0 },
    summary: { prs: 1, groups: [] },
  };
  const fake = fakeAsk(replies);
  const status = useStatus();

  try {
    scope.run(() => {
      const { refresh } = useSummary(fake.ask, status);

      return useOpeningProgress({ ask: fake.ask, status, localBusy: ref(false), refresh });
    });
    await flushPromises();
    status.error("Couldn't open: foo");
    replies.openingProgress = { active: false, opened: 0, planned: 0, metadataRevision: 1 };
    await vi.advanceTimersByTimeAsync(250);
    await flushPromises();

    assert.equal(fake.sent.filter((message) => message.type === "summary").length, 1);
    assert.equal(status.statusText.value, "Couldn't open: foo");
    assert.equal(status.isStatusError.value, true);
  } finally {
    scope.stop();
    vi.useRealTimers();
  }
});
