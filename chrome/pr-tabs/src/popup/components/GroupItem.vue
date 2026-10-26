<script setup lang="ts">
import AccordionContent, {
  type AccordionContentPassThroughOptions,
} from "primevue/accordioncontent";
import AccordionHeader, { type AccordionHeaderPassThroughOptions } from "primevue/accordionheader";
import AccordionPanel from "primevue/accordionpanel";
import Badge from "primevue/badge";
import { computed } from "vue";
import type { GroupItemProps, PrKey, PrRowEvents, PrRows } from "#/types";
import { useGroupItem } from "#/composables/useGroupItem";
import PrItem from "./PrItem.vue";

const props = defineProps<GroupItemProps>();
const emit = defineEmits<PrRowEvents>();
const { title, count, dotClass, countLabel } = useGroupItem(() => props.group);
const value = computed(() => props.group.id);
const prRows = computed<PrRows>(() =>
  Object.fromEntries(props.group.prs.map((row) => [row.key, row])),
);
const accordionContentPassThrough: AccordionContentPassThroughOptions = {
  content: { class: "p-0" },
};
const accordionHeaderPassThrough: AccordionHeaderPassThroughOptions = {
  root: { class: "-mx-popup-padding px-popup-padding" },
};
function handlePrGo(key: PrKey): void {
  emit("go", key);
}

function handlePrClose(key: PrKey): void {
  emit("close", key);
}
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
        <PrItem
          v-for="(row, key) in prRows"
          :key="key"
          :pr="row"
          :panes="panes"
          @go="handlePrGo"
          @close="handlePrClose"
        />
      </ul>
    </AccordionContent>
  </AccordionPanel>
</template>
