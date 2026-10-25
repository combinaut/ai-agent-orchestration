import { useAsyncState } from "@vueuse/core";
import { ref } from "vue";
import { failed } from "#/messages";
import type { Ask, BranchRef, PaneLookup, Panes, Status } from "#/types";

type PaneStatus = Pick<Status, "error">;

function closePopup(): void {
  window.close();
}

function usePaneLookup(
  ask: Ask,
  status: PaneStatus,
): Pick<Panes, "checking" | "lookup" | "ensure"> {
  let started = false;

  async function load(heads: readonly BranchRef[]): Promise<PaneLookup> {
    const reply = await ask({ type: "panes", heads });

    if (failed(reply)) {
      status.error(reply.error);

      return {};
    }

    return reply;
  }

  const lookupState = useAsyncState(load, {}, { immediate: false, resetOnExecute: false });

  async function ensure(heads: readonly BranchRef[]): Promise<void> {
    if (started || heads.length === 0) {
      return;
    }

    started = true;
    await lookupState.executeImmediate(heads);
  }

  return { checking: lookupState.isLoading, lookup: lookupState.state, ensure };
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

// One host lookup per popup: the first ensure asks, later ones reuse its answer.
export function usePanes(ask: Ask, status: PaneStatus, close: () => void = closePopup): Panes {
  return { ...usePaneLookup(ask, status), focus: usePaneFocus(ask, status, close) };
}
