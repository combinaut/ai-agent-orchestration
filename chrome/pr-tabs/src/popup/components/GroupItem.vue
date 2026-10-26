<script setup lang="ts">
import AccordionContent, {
  type AccordionContentPassThroughOptions,
} from "primevue/accordioncontent";
import AccordionHeader, { type AccordionHeaderPassThroughOptions } from "primevue/accordionheader";
import AccordionPanel from "primevue/accordionpanel";
import Badge from "primevue/badge";
import { computed } from "vue";
import type { GroupItemProps } from "#/types";
import { useGroupItem } from "#/composables/useGroupItem";
import PrItem from "./PrItem.vue";

const props = defineProps<GroupItemProps>();
const { title, count, dotClass, countLabel } = useGroupItem(() => props.group);
const value = computed(() => String(props.index));
const prRows = computed(() => props.group.prs);
const accordionContentPassThrough: AccordionContentPassThroughOptions = {
  content: { class: "p-0" },
};
const accordionHeaderPassThrough: AccordionHeaderPassThroughOptions = {
  root: { class: "-mx-popup-padding px-popup-padding" },
};
</script>

<template>
  <AccordionPanel :value="value">
    <AccordionHeader :pt="accordionHeaderPassThrough">
      <span class="flex flex-auto items-center gap-2">
        <span
          class="size-dot flex-none rounded-dot shadow-dot-ring"
          :class="dotClass"
          aria-hidden="true"
        />
        <span>{{ title }}</span>
        <Badge class="mr-2 ml-auto" severity="secondary" :value="count" :aria-label="countLabel" />
      </span>
    </AccordionHeader>
    <AccordionContent class="grid-cols-1" :pt="accordionContentPassThrough">
      <ul class="list-none p-0">
        <PrItem v-for="(row, rowIndex) in prRows" :key="rowIndex" :pr="row" :panes="panes" />
      </ul>
    </AccordionContent>
  </AccordionPanel>
</template>
