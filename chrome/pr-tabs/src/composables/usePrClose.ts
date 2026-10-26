import { useAsyncState } from "@vueuse/core";
import { nextTick } from "vue";
import { failed } from "#/messages";
import type { CloseFocus, PrClose, PrCloseContext, PrKey } from "#/types";

function closeButtons(): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll<HTMLButtonElement>("button[data-pr-close]")).filter(
    (button) => {
      const panel = button.closest(".p-accordionpanel");

      return !panel || panel.querySelector("[aria-expanded=true]") !== null;
    },
  );
}

function closeFocus(key: PrKey): CloseFocus {
  const before = closeButtons();
  const index = Math.max(
    0,
    before.findIndex((button) => button.dataset.prClose === key),
  );
  const preferred = before[index + 1]?.dataset.prClose ?? before[index - 1]?.dataset.prClose;

  return function restoreFocus(): void {
    const after = closeButtons();
    const next =
      after.find((button) => button.dataset.prClose === preferred) ?? after[index] ?? after.at(-1);
    const target = next ?? document.querySelector<HTMLInputElement>("#pr-search");
    target?.focus();
  };
}

export function usePrClose(context: PrCloseContext): PrClose {
  async function send(key: PrKey): Promise<void> {
    const focus = closeFocus(key);
    const result = await context.ask({ type: "closePr", key });

    if (failed(result)) {
      context.status.error(result.error);

      return;
    }

    await context.refresh();
    await nextTick();
    focus();
  }

  const { isLoading: busy, executeImmediate } = useAsyncState(send, undefined, {
    immediate: false,
  });

  async function close(key: PrKey): Promise<void> {
    if (!busy.value) {
      await executeImmediate(key);
    }
  }

  return { close };
}
