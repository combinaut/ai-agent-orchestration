<script setup lang="ts">
import Button from "primevue/button";
import Tag from "primevue/tag";
import { computed } from "vue";
import { usePrPane } from "#/composables/usePrPane";
import type { PrItemProps } from "#/types";
import PaneIcon from "./PaneIcon.vue";

const props = defineProps<PrItemProps>();
const label = computed(() => props.pr.label);
const title = computed(() => props.pr.title);
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

function handleGoClick(): void {
  if (props.pr.head) {
    void props.panes.focus(props.pr.head);
  }
}
</script>

<template>
  <li class="flex items-center gap-2 py-group-row">
    <span class="min-w-0 flex-auto truncate" :title="title">{{ label }} {{ title }}</span>
    <Tag
      v-if="isStateVisible"
      class="flex-none text-hint"
      :severity="stateSeverity"
      :value="stateText"
      :aria-label="stateLabel"
    />
    <span v-if="isGoVisible" class="flex-none" :title="goLabel">
      <Button
        size="small"
        severity="secondary"
        text
        rounded
        :aria-label="goLabel"
        :disabled="isGoDisabled"
        :loading="isChecking"
        @click="handleGoClick"
      >
        <template #icon>
          <span class="size-dot">
            <PaneIcon />
          </span>
        </template>
      </Button>
    </span>
  </li>
</template>
