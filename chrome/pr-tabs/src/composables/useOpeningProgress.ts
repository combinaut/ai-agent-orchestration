import { useIntervalFn } from "@vueuse/core";
import { ref, watch } from "vue";
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
  const revision = status.revision.value;
  await refresh();

  if (localBusy.value || status.revision.value !== revision) {
    return;
  }

  if (failed(result)) {
    status.error(result.error);
  } else {
    status.info(openAllText(result, status.statusText.value));
  }
}

function visibleProgress(
  reply: OpeningProgress,
  previous: OpeningProgress,
  local: boolean,
): OpeningProgress {
  if (!reply.active && local) {
    return { ...reply, opened: previous.opened, planned: previous.planned };
  }

  return reply;
}

function trackLocalStart(context: OpeningProgressContext, progress: Ref<OpeningProgress>): void {
  function handleLocalBusyChange(busy: boolean): void {
    if (busy && !progress.value.active) {
      progress.value = {
        active: false,
        opened: 0,
        planned: 0,
        metadataRevision: progress.value.metadataRevision,
      };
    }
  }

  watch(context.localBusy, handleLocalBusyChange, { flush: "sync" });
}

function progressAdvanced(reply: OpeningProgress, previous: OpeningProgress): boolean {
  const opened = reply.active && reply.opened > previous.opened;

  return opened || (reply.metadataRevision ?? 0) > (previous.metadataRevision ?? 0);
}

function observeProgress(
  context: OpeningProgressContext,
  progress: Ref<OpeningProgress>,
): OpeningProgressObserver {
  let observing = false;

  async function recordProgress(reply: OpeningProgress): Promise<void> {
    const local = context.localBusy.value;
    const advanced = progressAdvanced(reply, progress.value);
    progress.value = visibleProgress(reply, progress.value, local);

    if (advanced) {
      await context.refresh();
    }

    if (local || context.localBusy.value) {
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
  trackLocalStart(context, progress);
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
