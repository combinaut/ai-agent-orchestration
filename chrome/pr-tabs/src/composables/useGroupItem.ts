import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { isGroupColor, type GroupColor, type GroupSummary } from "#/core";

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

// One group row: its title, count, colour dot's class (unknown colours are grey) and count label.
export function useGroupItem(group: MaybeRefOrGetter<GroupSummary>) {
  const title = computed(() => toValue(group).title);
  const count = computed(() => toValue(group).count);
  const dotClass = computed(() => dotFor(toValue(group).color));
  const countLabel = computed(() => `${count.value} PR${count.value === 1 ? "" : "s"}`);

  return { title, count, dotClass, countLabel };
}
