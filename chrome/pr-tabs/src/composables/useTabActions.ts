import { useAsyncState } from "@vueuse/core";
import { checkText, duplicateText, openAllText } from "../core";
import { failed, type Replies, type RequestOf } from "../messages";
import type { Ask } from "./useServiceWorker";
import type { Status } from "./useStatus";

type ActionType = "openAll" | "checkNow" | "closeDuplicates";

export interface TabActionDeps {
  ask: Ask;
  status: Pick<Status, "statusText" | "info" | "error">;
  refresh: () => Promise<void>;
}

interface ActionSpec<K extends ActionType> {
  request: RequestOf<K>;
  pending: string;
  report: (result: Replies[K]) => string;
}

// One button: disabled while it runs, then refreshes the summary and reports its result.
function useTabAction<K extends ActionType>(
  { ask, status, refresh }: TabActionDeps,
  spec: ActionSpec<K>,
) {
  async function perform() {
    status.info(spec.pending);
    const result = await ask(spec.request);
    if (failed(result)) {
      status.error(result.error);
      return;
    }
    await refresh();
    status.info(spec.report(result));
  }
  const { isLoading: busy, executeImmediate } = useAsyncState(perform, undefined, {
    immediate: false,
  });
  async function run() {
    await executeImmediate();
  }
  return { busy, run };
}

function reportDuplicates({ closed }: Replies["closeDuplicates"]) {
  return duplicateText(closed);
}

// The three buttons: Open all, Close merged PRs and Close duplicate tabs.
export function useTabActions(deps: TabActionDeps) {
  function reportOpenAll(result: Replies["openAll"]) {
    return openAllText(result, deps.status.statusText.value);
  }
  const openAll = useTabAction(deps, {
    request: { type: "openAll" },
    pending: "Opening…",
    report: reportOpenAll,
  });
  const checkNow = useTabAction(deps, {
    request: { type: "checkNow" },
    pending: "Checking…",
    report: checkText,
  });
  const closeDuplicates = useTabAction(deps, {
    request: { type: "closeDuplicates" },
    pending: "Closing…",
    report: reportDuplicates,
  });
  return { openAll, checkNow, closeDuplicates };
}
