// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { effectScope, ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { test, vi } from "vitest";
import type { SummaryList } from "#/types";
import type { FakeReplies } from "#test/types";
import { useStatus } from "#/composables/useStatus";
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
