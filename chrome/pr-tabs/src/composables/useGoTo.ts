import { useAsyncState } from "@vueuse/core";
import { computed, ref, type Ref } from "vue";
import { failed, type RequestOf } from "../messages";
import type { Ask } from "./useServiceWorker";
import type { Status } from "./useStatus";

export interface GoToDeps {
  ask: Ask;
  status: Pick<Status, "error">;
  picker: { available: Readonly<Ref<boolean>>; repo: Ref<string> };
}

function closePopup() {
  window.close();
}

// Go to a PR: sends the input, with the picked repo while the dropdown is available, and closes the popup on success.
export function useGoTo({ ask, status, picker }: GoToDeps, input = ref(""), close = closePopup) {
  const isBlank = computed(() => !input.value.trim());
  function request(): RequestOf<"goTo"> {
    if (picker.available.value) {
      return { type: "goTo", input: input.value, repo: picker.repo.value };
    }
    return { type: "goTo", input: input.value };
  }
  async function send() {
    const result = await ask(request());
    if (failed(result)) {
      status.error(result.error);
      return;
    }
    close();
  }
  const { isLoading: busy, executeImmediate } = useAsyncState(send, undefined, {
    immediate: false,
  });
  async function go() {
    if (isBlank.value || busy.value) {
      return;
    }
    await executeImmediate();
  }
  return { input, busy, go };
}
