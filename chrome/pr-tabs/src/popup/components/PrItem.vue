<script setup lang="ts">
import Button from "primevue/button";
import Tag from "primevue/tag";
import { computed } from "vue";
import { usePrPane } from "#/composables/usePrPane";
import { highlightSegments } from "#/highlight";
import type { PrItemProps, PrRowEvents } from "#/types";
import { getMessage } from "#/i18n";
import CloseIcon from "./CloseIcon.vue";
import PaneIcon from "./PaneIcon.vue";
import PrTextSegment from "./PrTextSegment.vue";

const props = defineProps<PrItemProps>();
const emit = defineEmits<PrRowEvents>();
const key = computed(() => props.pr.key);
const tabId = computed(() => props.pr.tabId);
const closeLabel = computed(() => getMessage("closePrTab", { pr: props.pr.label }));
const title = computed(() => props.pr.title);
const number = computed(() => `#${props.pr.key.split("#").at(-1) ?? ""}`);
const rowLabel = computed(() => `${props.pr.label} ${props.pr.title}`.trim());
const numberSegments = computed(() => {
  const hash = props.pr.label.lastIndexOf("#");
  const labelMatches = props.pr.highlights?.label ?? [];
  const matches = labelMatches.flatMap((range) => {
    if (hash < 0 || range[1] < hash) {
      return [];
    }

    return [[Math.max(range[0], hash) - hash, range[1] - hash] as const];
  });

  return highlightSegments(number.value, matches, props.pr.highlights?.queryLength ?? 0);
});
const titleSegments = computed(() =>
  highlightSegments(
    title.value,
    props.pr.highlights?.title ?? [],
    props.pr.highlights?.queryLength ?? 0,
  ),
);
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
    <span class="pointer-events-none min-w-0 flex-auto truncate" :title="rowLabel">
      <template v-if="grouped">
        <span class="mr-2 font-mono text-muted-color tabular-nums">
          <PrTextSegment
            v-for="(segment, index) in numberSegments"
            :key="index"
            :segment="segment"
          />
        </span>
      </template>
      <span v-else class="mr-2 text-muted-color">{{ pr.label }}</span>
      <PrTextSegment v-for="(segment, index) in titleSegments" :key="index" :segment="segment" />
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
      class="relative mr-2 flex-none"
      size="small"
      severity="secondary"
      text
      rounded
      :data-pr-close="key"
      :data-pr-close-id="tabId"
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
