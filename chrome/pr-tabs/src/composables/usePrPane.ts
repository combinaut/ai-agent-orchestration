import { computed, toValue, type ComputedRef, type MaybeRefOrGetter } from "vue";
import { headKey } from "#/core";
import { getMessage, getPlainMessage } from "#/i18n";
import type {
  AgentState,
  PaneAvailability,
  PaneMatch,
  Panes,
  PrPane,
  PrRow,
  TagSeverity,
} from "#/types";

const SEVERITIES: Readonly<Record<AgentState, TagSeverity>> = {
  working: "info",
  idle: "secondary",
  done: "success",
  waiting: "warn",
};

function stateName(state: AgentState | null): string {
  const names: Readonly<Record<AgentState, string>> = {
    working: getPlainMessage("agentWorking"),
    idle: getPlainMessage("agentIdle"),
    done: getPlainMessage("agentDone"),
    waiting: getPlainMessage("agentWaiting"),
  };

  return state ? names[state] : "";
}

function availabilityOf(
  hasHead: boolean,
  checking: boolean,
  match: PaneMatch | null | undefined,
): PaneAvailability {
  if (!hasHead) {
    return "missing";
  }

  if (match !== undefined) {
    return match ? "found" : "missing";
  }

  return checking ? "checking" : "unchecked";
}

function goLabelFor(availability: PaneAvailability, pr: string): string {
  if (availability === "found") {
    return getMessage("goToPane", { pr });
  }

  if (availability === "checking") {
    return getMessage("checkingPane", { pr });
  }

  return getMessage("noPane", { pr });
}

function useMatch(
  row: MaybeRefOrGetter<PrRow>,
  panes: Panes,
): ComputedRef<PaneMatch | null | undefined> {
  return computed(() => {
    const head = toValue(row).head;

    return head ? panes.lookup.value[headKey(head)] : undefined;
  });
}

// The go to pane button and agent state tag of one PR row.
export function usePrPane(row: MaybeRefOrGetter<PrRow>, panes: Panes): PrPane {
  const match = useMatch(row, panes);
  const availability = computed(() =>
    availabilityOf(Boolean(toValue(row).head), panes.checking.value, match.value),
  );
  const state = computed(() => match.value?.state ?? null);
  const stateText = computed(() => stateName(state.value));

  return {
    availability,
    isGoVisible: computed(
      () => availability.value === "found" || availability.value === "checking",
    ),
    isGoDisabled: computed(() => availability.value !== "found"),
    isChecking: computed(() => availability.value === "checking"),
    goLabel: computed(() => goLabelFor(availability.value, toValue(row).label)),
    state,
    isStateVisible: computed(() => state.value !== null),
    stateText,
    stateLabel: computed(() => getMessage("agentState", { state: stateText.value })),
    stateSeverity: computed(() => SEVERITIES[state.value ?? "idle"]),
  };
}
