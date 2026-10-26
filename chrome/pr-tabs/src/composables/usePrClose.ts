import { useAsyncState } from "@vueuse/core";
import { nextTick } from "vue";
import { failed } from "#/messages";
import type { CloseFocus, PrClose, PrCloseContext, PrKey, TabId } from "#/types";

function closeButtons(): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll<HTMLButtonElement>("button[data-pr-close]")).filter(
    (button) => {
      const panel = button.closest(".p-accordionpanel");

      return !panel || panel.querySelector("[aria-expanded=true]") !== null;
    },
  );
}

function closeFocus(tabId: TabId): CloseFocus {
  const before = closeButtons();
  const index = Math.max(
    0,
    before.findIndex((button) => button.dataset.prCloseId === String(tabId)),
  );
  const preferred = before[index + 1]?.dataset.prCloseId ?? before[index - 1]?.dataset.prCloseId;

  return function restoreFocus(): void {
    const after = closeButtons();
    const next =
      after.find((button) => button.dataset.prCloseId === preferred) ??
      after[index] ??
      after.at(-1);
    const target = next ?? document.querySelector<HTMLInputElement>("#pr-search");
    target?.focus();
  };
}

function useCloseQueue(send: PrClose["close"]): PrClose["close"] {
  const pending = new Map<PrKey, Promise<void>>();
  let closing: Promise<void> = Promise.resolve();

  function close(key: PrKey, tabId: TabId): Promise<void> {
    const existing = pending.get(key);

    if (existing) {
      return existing;
    }

    async function closeNow(): Promise<void> {
      await send(key, tabId);
    }

    function forget(): void {
      pending.delete(key);
    }

    const queued = closing.then(closeNow, closeNow).finally(forget);
    pending.set(key, queued);
    closing = queued;

    return queued;
  }

  return close;
}

export function usePrClose(context: PrCloseContext): PrClose {
  async function send(key: PrKey, tabId: TabId): Promise<void> {
    const focus = closeFocus(tabId);
    const result = await context.ask({ type: "closePr", key, tabId });

    if (failed(result)) {
      context.status.error(result.error);

      return;
    }

    await context.refresh();
    await nextTick();
    focus();
  }

  const { executeImmediate } = useAsyncState(send, undefined, {
    immediate: false,
  });

  return { close: useCloseQueue(executeImmediate) };
}
