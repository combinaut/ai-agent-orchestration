<script setup lang="ts">
import Button from "primevue/button";
import Tag from "primevue/tag";
import { computed } from "vue";
import { prNumberText } from "#/composables/useSearchFilter";
import { usePrPane } from "#/composables/usePrPane";
import { highlightSegments } from "#/highlight";
import type { PrItemProps, PrRowEvents } from "#/types";
import { getMessage } from "#/i18n";
import PaneIcon from "./PaneIcon.vue";
import PrTextSegment from "./PrTextSegment.vue";

const props = defineProps<PrItemProps>();
const emit = defineEmits<PrRowEvents>();
const key = computed(() => props.pr.key);
const tabId = computed(() => props.pr.tabId);
const rowId = computed(() => `pr-${props.pr.tabId}`);
const isSelected = computed(() => props.selected === rowId.value);
const rowClass = computed(() => ({ "pr-row-selected": isSelected.value }));
const number = computed(() => prNumberText(props.pr));
const closeLabel = computed(() => getMessage("closePrTab", { pr: number.value }));
const title = computed(() => props.pr.title);
const rowLabel = computed(() => `${props.pr.label} ${props.pr.title}`.trim());
const numberSegments = computed(() => highlightSegments(number.value, props.pr.highlights?.number));
const titleSegments = computed(() => highlightSegments(title.value, props.pr.highlights?.title));
const {
  isGoVisible,
  isGoDisabled,
  isChecking,
  goLabel,
  isStateVisible,
  stateText,
  stateLabel,
  stateSeverity,
} = usePrPane(() => props.pr, props.panes);

function handleCloseClick(): void {
  emit("close", props.pr.key, props.pr.tabId);
}

function handleRowClick(): void {
  emit("go", props.pr.key);
}

function handleRowMouseenter(): void {
  emit("hover", props.pr.tabId);
}

function handleRowKeydown(event: KeyboardEvent): void {
  if (event.key === "Delete" || event.key === "Backspace") {
    event.preventDefault();
    handleCloseClick();
  }
}

function handleGoClick(): void {
  if (props.pr.head) {
    void props.panes.focus(props.pr.head);
  }
}
</script>

<template>
  <li
    :id="rowId"
    class="pr-row"
    :class="rowClass"
    role="option"
    :aria-selected="isSelected"
    @mouseenter="handleRowMouseenter"
  >
    <button
      data-pr-tab
      type="button"
      class="pr-row-button"
      :aria-label="rowLabel"
      :title="title"
      @click="handleRowClick"
      @keydown="handleRowKeydown"
    />
    <span class="pr-number">
      <PrTextSegment v-for="(segment, index) in numberSegments" :key="index" :segment="segment" />
    </span>
    <span class="pr-title" :title="rowLabel">
      <PrTextSegment v-for="(segment, index) in titleSegments" :key="index" :segment="segment" />
    </span>
    <Tag
      v-if="isStateVisible"
      class="pr-state"
      :severity="stateSeverity"
      :value="stateText"
      :aria-label="stateLabel"
    />
    <span v-if="isGoVisible" class="pr-pane" :title="goLabel">
      <Button
        data-pr-pane
        size="small"
        severity="secondary"
        text
        class="pr-pane-button"
        :aria-label="goLabel"
        :disabled="isGoDisabled"
        :loading="isChecking"
        @click.stop="handleGoClick"
      >
        <template #icon>
          <span class="pr-pane-icon">
            <PaneIcon />
          </span>
        </template>
      </Button>
    </span>
    <kbd v-if="isSelected" class="pr-key-hint" aria-hidden="true">↵</kbd>
    <button
      type="button"
      class="pr-close"
      :data-pr-close="key"
      :data-pr-close-id="tabId"
      :aria-label="closeLabel"
      :title="closeLabel"
      @click.stop="handleCloseClick"
    >
      <i class="pi pi-times" aria-hidden="true" />
    </button>
  </li>
</template>
