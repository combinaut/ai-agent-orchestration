import { computed, readonly, ref } from "vue";

export type StatusLine =
  | { readonly kind: "info"; readonly text: string }
  | { readonly kind: "error"; readonly text: string };
export type Status = ReturnType<typeof useStatus>;

// The popup's one status line: a plain text or an error.
export function useStatus() {
  const status = ref<StatusLine>({ kind: "info", text: "" });
  const statusText = computed(() => status.value.text);
  const isStatusError = computed(() => status.value.kind === "error");
  function info(text: string) {
    status.value = { kind: "info", text };
  }
  function error(text: string) {
    status.value = { kind: "error", text };
  }
  return { status: readonly(status), statusText, isStatusError, info, error };
}
