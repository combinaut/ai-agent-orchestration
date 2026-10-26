import { useAsyncState } from "@vueuse/core";
import { ref } from "vue";
import { headKey, MAX_PANE_LOOKUPS } from "#/core";
import { failed } from "#/messages";
import type { Ask, BranchRef, HeadKey, PaneLoader, PaneLookup, Panes, Status } from "#/types";

type PaneStatus = Pick<Status, "error">;

function closePopup(): void {
  window.close();
}

function usePaneLookup(
  ask: Ask,
  status: PaneStatus,
): Pick<Panes, "checking" | "lookup" | "ensure"> {
  async function load(heads: readonly BranchRef[]): Promise<PaneLookup> {
    const reply = await ask({ type: "panes", heads });

    if (failed(reply)) {
      status.error(reply.error);

      return lookupState.state.value;
    }

    return { ...lookupState.state.value, ...reply };
  }

  const lookupState = useAsyncState(load, {}, { immediate: false, resetOnExecute: false });

  const ensure = usePaneQueue(lookupState.executeImmediate);

  return { checking: lookupState.isLoading, lookup: lookupState.state, ensure };
}

function enqueueHeads(
  heads: readonly BranchRef[],
  requested: Set<HeadKey>,
  pending: Map<HeadKey, BranchRef>,
): void {
  for (const head of heads) {
    const key = headKey(head);

    if (!requested.has(key) && requested.size < MAX_PANE_LOOKUPS) {
      requested.add(key);
      pending.set(key, head);
    }
  }
}

function usePaneQueue(load: PaneLoader): Panes["ensure"] {
  const requested = new Set<HeadKey>();
  const pending = new Map<HeadKey, BranchRef>();
  let reading: Promise<void> | undefined;

  async function drain(): Promise<void> {
    try {
      while (pending.size > 0) {
        const heads = [...pending.values()];
        pending.clear();
        await load(heads);
      }
    } finally {
      reading = undefined;
    }
  }

  function ensure(heads: readonly BranchRef[]): Promise<void> {
    enqueueHeads(heads, requested, pending);

    if (pending.size > 0) {
      reading ??= drain();
    }

    return reading ?? Promise.resolve();
  }

  return ensure;
}

function usePaneFocus(ask: Ask, status: PaneStatus, close: () => void): Panes["focus"] {
  const focusing = ref(false);

  return async function focus(head: BranchRef): Promise<void> {
    if (focusing.value) {
      return;
    }

    focusing.value = true;

    const reply = await ask({ type: "focusPane", head });

    focusing.value = false;

    if (failed(reply)) {
      status.error(reply.error);

      return;
    }

    close();
  };
}

// Each popup looks up at most MAX_PANE_LOOKUPS distinct heads, one request at a time.
export function usePanes(ask: Ask, status: PaneStatus, close: () => void = closePopup): Panes {
  return { ...usePaneLookup(ask, status), focus: usePaneFocus(ask, status, close) };
}
