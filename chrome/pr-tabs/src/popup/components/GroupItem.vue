<script setup lang="ts">
import AccordionContent, {
  type AccordionContentPassThroughOptions,
} from "primevue/accordioncontent";
import AccordionHeader, { type AccordionHeaderPassThroughOptions } from "primevue/accordionheader";
import AccordionPanel from "primevue/accordionpanel";
import Badge from "primevue/badge";
import { computed } from "vue";
import type { GroupItemProps, PrKey, TabId, PrRowEvents, PrRows } from "#/types";
import { useGroupItem } from "#/composables/useGroupItem";
import PrItem from "./PrItem.vue";

const props = defineProps<GroupItemProps>();
const emit = defineEmits<PrRowEvents>();
const { title, count, countLabel } = useGroupItem(() => props.group);
const value = computed(() => props.group.id);
const prRows = computed<PrRows>(() =>
  Object.fromEntries(props.group.prs.map((row) => [row.key, row])),
);
const accordionContentPassThrough: AccordionContentPassThroughOptions = {
  content: { class: "p-0" },
};
const accordionHeaderPassThrough: AccordionHeaderPassThroughOptions = {
  root: {
    class:
      "h-8 rounded-md px-1 pl-3 text-[var(--fg)] hover:bg-[var(--hover)] focus-visible:shadow-[inset_0_0_0_2px_var(--accent)]",
  },
};
const railClass = computed(() => `group-${props.group.color ?? "grey"}`);
function handlePrGo(key: PrKey): void {
  emit("go", key);
}

function handlePrClose(key: PrKey, tabId: TabId): void {
  emit("close", key, tabId);
}
</script>

<template>
  <AccordionPanel :value="value" :class="railClass">
    <AccordionHeader :pt="accordionHeaderPassThrough">
      <span class="flex flex-auto items-center">
        <span class="min-w-0 truncate font-semibold">{{ title }}</span>
        <Badge
          class="mr-2 ml-auto h-[18px] min-w-5 rounded-full bg-[var(--badge)] px-1.5 font-mono text-[11px] leading-[18px] text-[var(--muted)]"
          severity="secondary"
          :value="count"
          :aria-label="countLabel"
        />
      </span>
    </AccordionHeader>
    <AccordionContent class="grid-cols-1" :pt="accordionContentPassThrough">
      <ul class="list-none p-0">
        <PrItem
          v-for="(row, key) in prRows"
          :key="key"
          :pr="row"
          :panes="panes"
          grouped
          @go="handlePrGo"
          @close="handlePrClose"
        />
      </ul>
    </AccordionContent>
  </AccordionPanel>
</template>
