import { useAsyncState } from "@vueuse/core";
import { checkText, duplicateText, openAllText } from "#/core";
import { failed } from "#/messages";
import type {
  Replies,
  TabAction,
  TabActionContext,
  TabActions,
  TabActionSpec,
  TabActionType,
} from "#/types";

// One button: disabled while it runs, then refreshes the summary and reports its result.
function useTabAction<K extends TabActionType>(
  context: TabActionContext,
  spec: TabActionSpec<K>,
): TabAction {
  const { ask, status, refresh } = context;

  async function perform(): Promise<void> {
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

  async function run(): Promise<void> {
    await executeImmediate();
  }

  return { busy, run };
}

function reportDuplicates(result: Replies["closeDuplicates"]): string {
  return duplicateText(result.closed);
}

// The three buttons: Open all, Close merged PRs and Close duplicate tabs.
export function useTabActions(context: TabActionContext): TabActions {
  function reportOpenAll(result: Replies["openAll"]): string {
    return openAllText(result, context.status.statusText.value);
  }

  const openAll = useTabAction(context, {
    request: { type: "openAll" },
    pending: "Opening…",
    report: reportOpenAll,
  });

  const checkNow = useTabAction(context, {
    request: { type: "checkNow" },
    pending: "Checking…",
    report: checkText,
  });

  const closeDuplicates = useTabAction(context, {
    request: { type: "closeDuplicates" },
    pending: "Closing…",
    report: reportDuplicates,
  });

  return { openAll, checkNow, closeDuplicates };
}
