<script setup lang="ts">
import Button from "primevue/button";
import Tag from "primevue/tag";
import { rowSegments } from "#/highlight";
import PrTextSegment from "./PrTextSegment.vue";
import { computed } from "vue";
import { usePrPane } from "#/composables/usePrPane";
import type { PrItemProps, PrRowEvents } from "#/types";
import { getMessage } from "#/i18n";
import CloseIcon from "./CloseIcon.vue";
import PaneIcon from "./PaneIcon.vue";

const props = defineProps<PrItemProps>();
const emit = defineEmits<PrRowEvents>();
const key = computed(() => props.pr.key);
const closeLabel = computed(() => getMessage("closePrTab", { pr: props.pr.label }));
const segments = computed(() => rowSegments(props.pr));
const title = computed(() => props.pr.title);
const rowLabel = computed(() => `${props.pr.label} ${props.pr.title}`.trim());
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
  emit("close", props.pr.key);
}

function handleRowClick(): void {
  emit("go", props.pr.key);
}

function handleGoClick(): void {
  if (props.pr.head) {
    void props.panes.focus(props.pr.head);
  }
}
</script>

<template>
  <li
    class="relative -mx-popup-padding flex min-h-pr-row cursor-pointer items-center gap-2 px-popup-padding py-group-row focus-within:bg-row-hover hover:bg-row-hover"
  >
    <button
      data-pr-tab
      type="button"
      class="absolute inset-0 size-full cursor-pointer"
      :aria-label="rowLabel"
      :title="title"
      @click="handleRowClick"
    />
    <span class="pointer-events-none min-w-0 flex-auto truncate">
      <PrTextSegment v-for="(segment, index) in segments" :key="index" :segment="segment" />
    </span>
    <Tag
      v-if="isStateVisible"
      class="pointer-events-none flex-none text-hint"
      :severity="stateSeverity"
      :value="stateText"
      :aria-label="stateLabel"
    />
    <span v-if="isGoVisible" class="relative flex-none" :title="goLabel">
      <Button
        data-pr-pane
        size="small"
        severity="secondary"
        text
        rounded
        :aria-label="goLabel"
        :disabled="isGoDisabled"
        :loading="isChecking"
        @click.stop="handleGoClick"
      >
        <template #icon>
          <span class="size-pane-icon">
            <PaneIcon />
          </span>
        </template>
      </Button>
    </span>
    <Button
      class="relative flex-none"
      size="small"
      severity="secondary"
      text
      rounded
      :data-pr-close="key"
      :aria-label="closeLabel"
      :title="closeLabel"
      @click.stop="handleCloseClick"
    >
      <template #icon>
        <span class="size-pane-icon"><CloseIcon /></span>
      </template>
    </Button>
  </li>
</template>
