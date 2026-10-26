import { useIntervalFn } from "@vueuse/core";
import { ref } from "vue";
import type { Ref } from "vue";
import { failed } from "#/messages";
import { openAllText } from "#/texts";
import type {
  OpeningProgress,
  OpeningProgressContext,
  OpeningProgressObserver,
  Reply,
} from "#/types";

async function reportCompletion(
  context: OpeningProgressContext,
  result: Reply<"openAll">,
): Promise<void> {
  const { status, refresh, localBusy } = context;
  await refresh();

  if (localBusy.value) {
    return;
  }

  if (failed(result)) {
    status.error(result.error);
  } else {
    status.info(openAllText(result, status.statusText.value));
  }
}

function observeProgress(
  context: OpeningProgressContext,
  progress: Ref<OpeningProgress>,
): OpeningProgressObserver {
  let observing = false;

  async function recordProgress(reply: OpeningProgress): Promise<void> {
    progress.value = reply;

    if (context.localBusy.value) {
      observing = false;
    } else if (reply.active) {
      observing = true;
    } else if (observing && reply.result) {
      observing = false;
      await reportCompletion(context, reply.result);
    }
  }

  return recordProgress;
}

export function useOpeningProgress(
  context: OpeningProgressContext,
): Readonly<Ref<OpeningProgress>> {
  const { ask } = context;
  const progress = ref<OpeningProgress>({ active: false, opened: 0, planned: 0 });
  const recordProgress = observeProgress(context, progress);
  let reading = false;

  async function handleProgressTick(): Promise<void> {
    if (reading) {
      return;
    }

    reading = true;

    try {
      const reply = await ask({ type: "openingProgress" });

      if (!failed(reply)) {
        await recordProgress(reply);
      }
    } finally {
      reading = false;
    }
  }

  useIntervalFn(handleProgressTick, 250, { immediateCallback: true });

  return progress;
}
