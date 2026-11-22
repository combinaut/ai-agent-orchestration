<script setup lang="ts">
import AccordionContent from "primevue/accordioncontent";
import AccordionHeader from "primevue/accordionheader";
import AccordionPanel from "primevue/accordionpanel";
import Badge from "primevue/badge";
import { computed } from "vue";
import { highlightSegments } from "#/highlight";
import type { GroupEvents, GroupItemProps, PrKey, PrRows, TabId } from "#/types";
import { useGroupItem } from "#/composables/useGroupItem";
import PrItem from "./PrItem.vue";
import PrTextSegment from "./PrTextSegment.vue";

const props = defineProps<GroupItemProps>();
const emit = defineEmits<GroupEvents>();
const { title, count, colorClass, countLabel } = useGroupItem(() => props.group);
const value = computed(() => props.group.id);
const badge = computed(() =>
  props.group.total === undefined ? String(count.value) : `${count.value}/${props.group.total}`,
);
const chevronClass = computed(() =>
  props.collapsed.has(props.group.id) ? "pi-chevron-right" : "pi-chevron-down",
);
const titleSegments = computed(() => highlightSegments(title.value, props.group.titleMatches));
const prRows = computed<PrRows>(() =>
  Object.fromEntries(props.group.prs.map((row) => [row.key, row])),
);

function handlePrGo(key: PrKey): void {
  emit("go", key);
}

function handlePrClose(key: PrKey, tabId: TabId): void {
  emit("close", key, tabId);
}

function handleHeaderKeydown(event: KeyboardEvent): void {
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    emit("collapse", props.group.id, event.key === "ArrowLeft");
  }
}
</script>

<template>
  <AccordionPanel :value="value" class="group-panel" :class="colorClass">
    <AccordionHeader class="group-header" @keydown="handleHeaderKeydown">
      <span class="group-name">
        <PrTextSegment v-for="(segment, index) in titleSegments" :key="index" :segment="segment" />
      </span>
      <span class="group-spacer" />
      <Badge class="group-badge" :value="badge" :aria-label="countLabel" />
      <template #toggleicon>
        <span class="group-chevron">
          <i class="pi" :class="chevronClass" aria-hidden="true" />
        </span>
      </template>
    </AccordionHeader>
    <AccordionContent>
      <ul class="group-rows" role="group">
        <PrItem
          v-for="(row, key) in prRows"
          :key="key"
          :pr="row"
          :panes="panes"
          :selected="selected"
          @go="handlePrGo"
          @close="handlePrClose"
        />
      </ul>
    </AccordionContent>
  </AccordionPanel>
</template>
