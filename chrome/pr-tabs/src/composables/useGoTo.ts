import { useAsyncState } from "@vueuse/core";
import { computed, ref, type Ref } from "vue";
import { failed } from "#/messages";
import type { GoTo, GoToContext, GoToRequest } from "#/types";

function closePopup(): void {
  window.close();
}

// The picked repo goes along only while the dropdown is available.
function goToRequest(input: string, picker: GoToContext["picker"]): GoToRequest {
  if (picker.available.value) {
    return { type: "goTo", input, repo: picker.repo.value };
  }

  return { type: "goTo", input };
}

// Go to a PR: sends the input, with the picked repo while the dropdown is available, and closes the
// popup on success.
export function useGoTo(
  context: GoToContext,
  input: Ref<string> = ref(""),
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
