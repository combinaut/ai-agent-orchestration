import { computed, ref } from "vue";
import type { Status, StatusLine } from "#/types";

export function useStatus(): Status {
  const status = ref<StatusLine>({ kind: "info", text: "" });
  const statusText = computed(() => status.value.text);
  const isStatusError = computed(() => status.value.kind === "error");

  function info(text: string): void {
    status.value = { kind: "info", text };
  }

  function error(text: string): void {
    status.value = { kind: "error", text };
  }

  return { statusText, isStatusError, info, error };
}
