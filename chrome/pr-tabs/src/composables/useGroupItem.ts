import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { isGroupColor } from "#/core";
import { getPluralMessage } from "#/i18n";
import type { GroupColor, GroupRow, GroupSummary } from "#/types";

// The class that sets --group-color.
export function groupColorClass(color: string | undefined): `group-${GroupColor}` {
  return `group-${isGroupColor(color) ? color : "grey"}`;
}

export function useGroupItem(group: MaybeRefOrGetter<GroupSummary>): GroupRow {
  const title = computed(() => toValue(group).title);
  const count = computed(() => toValue(group).count);
  const colorClass = computed(() => groupColorClass(toValue(group).color));
  const countLabel = computed(() => getPluralMessage("prCount", count.value));

  return { title, count, colorClass, countLabel };
}
