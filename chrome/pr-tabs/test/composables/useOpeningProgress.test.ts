// @vitest-environment happy-dom
import assert from "node:assert/strict";
import { effectScope } from "vue";
import { flushPromises } from "@vue/test-utils";
import { test, vi } from "vitest";
import { useOpeningProgress } from "#/composables/useOpeningProgress";
import { fakeAsk } from "./fakeAsk";

test("popup reads ongoing progress and stops polling when its scope closes", async () => {
  vi.useFakeTimers();
  const scope = effectScope();
  const fake = fakeAsk({ openingProgress: { active: true, opened: 3, planned: 10 } });

  try {
    const progress = scope.run(() => useOpeningProgress(fake.ask));
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
