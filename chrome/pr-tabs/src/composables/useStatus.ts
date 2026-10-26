import { computed, ref } from "vue";
import type { Status, StatusKind, StatusLine, StatusRevision } from "#/types";

export function useStatus(): Status {
  const status = ref<StatusLine>({ kind: "info", text: "" });
  const revision = ref<StatusRevision>(0);
  const statusText = computed(() => status.value.text);
  const isStatusError = computed(() => status.value.kind === "error");

  function update(kind: StatusKind, text: string, expected?: StatusRevision): void {
    if (expected !== undefined && expected !== revision.value) {
      return;
    }

    if (expected === undefined) {
      revision.value++;
    }

    status.value = { kind, text };
  }

  function info(text: string, expected?: StatusRevision): void {
    update("info", text, expected);
  }

  function error(text: string, expected?: StatusRevision): void {
    update("error", text, expected);
  }

  return { statusText, isStatusError, revision, info, error };
}
