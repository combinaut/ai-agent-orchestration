import { useAsyncState } from "@vueuse/core";
import { computed, type Ref } from "vue";
import { failed } from "#/messages";
import type { GoTo, GoToContext, GoToRequest } from "#/types";

function closePopup(): void {
  window.close();
}

function goToRequest(input: string, picker: GoToContext["picker"]): GoToRequest {
  if (picker.available.value) {
    return { type: "goTo", input, repo: picker.repo.value };
  }

  return { type: "goTo", input };
}

export function useGoTo(
  context: GoToContext,
  input: Ref<string>,
  close: () => void = closePopup,
): GoTo {
  const { ask, status, picker } = context;
  const isBlank = computed(() => !input.value.trim());

  async function send(): Promise<void> {
    const result = await ask(goToRequest(input.value, picker));

    if (failed(result)) {
      status.error(result.error);

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

    await executeImmediate();
  }

  return { input, busy, go };
}
