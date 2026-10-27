import { useAsyncState } from "@vueuse/core";
import { computed, type Ref } from "vue";
import { failed } from "#/messages";
import type { Failure, GoTo, GoToContext, GoToRequest, PrKey, PrNavigationRequest } from "#/types";

function closePopup(): void {
  window.close();
}

function goToRequest(input: string, picker: GoToContext["picker"]): GoToRequest {
  if (picker.available.value) {
    return { type: "goTo", input, repo: picker.repo.value };
  }

  return { type: "goTo", input };
}

async function showFailure(context: GoToContext, failure: Failure): Promise<void> {
  if (failure.tabsOpened) {
    await context.refresh();
  }

  context.status.error(failure.error);
}

export function useGoTo(
  context: GoToContext,
  input: Ref<string>,
  close: () => void = closePopup,
): GoTo {
  const isBlank = computed(() => !input.value.trim());

  async function send(request: PrNavigationRequest): Promise<void> {
    const result = await context.ask(request);

    if (failed(result)) {
      await showFailure(context, result);

      return;
    }

    close();
  }

  const { isLoading: busy, executeImmediate } = useAsyncState(send, undefined, {
    immediate: false,
  });

  async function go(): Promise<void> {
    if (isBlank.value || busy.value) {
      return;
    }

    await executeImmediate(goToRequest(input.value, context.picker));
  }

  async function focusPr(key: PrKey): Promise<void> {
    if (!busy.value) {
      await executeImmediate({ type: "focusPr", key });
    }
  }

  return { input, busy, go, focusPr };
}
