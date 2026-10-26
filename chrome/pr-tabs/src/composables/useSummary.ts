import { useAsyncState } from "@vueuse/core";
import { failed } from "#/messages";
import { summaryText } from "#/texts";
import type { Ask, GroupSummary, Status, StatusRevision, SummaryList } from "#/types";

// A failed refresh keeps the groups it had.
export function useSummary(
  ask: Ask,
  status: Pick<Status, "info" | "error" | "revision">,
): SummaryList {
  async function loadGroups(revision: StatusRevision): Promise<GroupSummary[]> {
    const summary = await ask({ type: "summary" });

    if (failed(summary)) {
      status.error(summary.error, revision);

      return groups.value;
    }

    status.info(summaryText(summary), revision);

    return summary.groups;
  }

  const { state: groups, executeImmediate } = useAsyncState(loadGroups, [], {
    immediate: false,
    resetOnExecute: false,
  });

  let refreshing: Promise<void> = Promise.resolve();

  function refresh(): Promise<void> {
    const revision = status.revision.value;

    async function refreshNow(): Promise<void> {
      await executeImmediate(revision);
    }

    refreshing = refreshing.then(refreshNow, refreshNow);

    return refreshing;
  }

  return { groups, refresh };
}
