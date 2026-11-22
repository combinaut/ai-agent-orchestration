import { computed, ref } from "vue";
import type { Status, StatusLine, StatusRevision } from "#/types";

export function useStatus(): Status {
  const status = ref<StatusLine>({ kind: "info", text: "" });
  const revision = ref<StatusRevision>(0);
  const statusText = computed(() => status.value.text);
  const statusKind = computed(() => status.value.kind);
  const isStatusError = computed(() => status.value.kind === "error");

  function show(line: StatusLine, expected?: StatusRevision): void {
    if (expected !== undefined && expected !== revision.value) {
      return;
    }

    if (expected === undefined) {
      revision.value++;
    }

    status.value = line;
  }

  function info(text: string, expected?: StatusRevision): void {
    show({ kind: "info", text }, expected);
  }

  function error(text: string, expected?: StatusRevision): void {
    show({ kind: "error", text }, expected);
  }

  return { statusText, statusKind, isStatusError, revision, show, info, error };
}
