import { useAsyncState } from "@vueuse/core";
import { summaryText, type GroupSummary } from "../core";
import { failed } from "../messages";
import type { Ask } from "./useServiceWorker";
import type { Status } from "./useStatus";

// The open PR count in the status line and the PR tab groups below it; a failed refresh keeps the groups.
export function useSummary(ask: Ask, status: Pick<Status, "info" | "error">) {
  async function loadGroups(): Promise<GroupSummary[]> {
    const summary = await ask({ type: "summary" });
    if (failed(summary)) {
      status.error(summary.error);
      return groups.value;
    }
    status.info(summaryText(summary));
    return summary.groups;
  }
  const { state: groups, executeImmediate } = useAsyncState(loadGroups, [], {
    immediate: false,
    resetOnExecute: false,
  });
  async function refresh() {
    await executeImmediate();
  }
  return { groups, refresh };
}
