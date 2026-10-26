import { useAsyncState } from "@vueuse/core";
import { failed } from "#/messages";
import { summaryText } from "#/texts";
import type { Ask, GroupSummary, Status, SummaryList } from "#/types";

// A failed refresh keeps the groups it had.
export function useSummary(ask: Ask, status: Pick<Status, "info" | "error">): SummaryList {
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

  let refreshing: Promise<void> = Promise.resolve();

  async function refreshNow(): Promise<void> {
    await executeImmediate();
  }

  function refresh(): Promise<void> {
    refreshing = refreshing.then(refreshNow, refreshNow);

    return refreshing;
  }

  return { groups, refresh };
}
