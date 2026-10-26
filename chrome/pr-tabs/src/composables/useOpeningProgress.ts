import { useIntervalFn } from "@vueuse/core";
import { ref } from "vue";
import type { Ref } from "vue";
import { failed } from "#/messages";
import type { Ask, OpeningProgress } from "#/types";

export function useOpeningProgress(ask: Ask): Readonly<Ref<OpeningProgress>> {
  const progress = ref<OpeningProgress>({ active: false, opened: 0, planned: 0 });
  let reading = false;

  async function handleProgressTick(): Promise<void> {
    if (reading) {
      return;
    }

    reading = true;

    try {
      const reply = await ask({ type: "openingProgress" });

      if (!failed(reply)) {
        progress.value = reply;
      }
    } finally {
      reading = false;
    }
  }

  useIntervalFn(handleProgressTick, 250, { immediateCallback: true });

  return progress;
}
