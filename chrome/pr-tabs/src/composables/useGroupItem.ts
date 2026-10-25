import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { isGroupColor } from "#/core";
import { getPluralMessage } from "#/i18n";
import type { GroupColor, GroupRow, GroupSummary } from "#/types";

const DOTS: Readonly<Record<GroupColor, string>> = {
  grey: "bg-group-grey",
  blue: "bg-group-blue",
  red: "bg-group-red",
  yellow: "bg-group-yellow",
  green: "bg-group-green",
  pink: "bg-group-pink",
  purple: "bg-group-purple",
  cyan: "bg-group-cyan",
  orange: "bg-group-orange",
};

function dotFor(color: string | undefined): string {
  return isGroupColor(color) ? DOTS[color] : DOTS.grey;
}

export function useGroupItem(group: MaybeRefOrGetter<GroupSummary>): GroupRow {
  const title = computed(() => toValue(group).title);
  const count = computed(() => toValue(group).count);
  const dotClass = computed(() => dotFor(toValue(group).color));
  const countLabel = computed(() => getPluralMessage("prCount", count.value));

  return { title, count, dotClass, countLabel };
}
