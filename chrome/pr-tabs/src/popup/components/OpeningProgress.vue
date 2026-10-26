<script setup lang="ts">
import ProgressBar from "primevue/progressbar";
import { computed } from "vue";
import { getMessage, getPlainMessage } from "#/i18n";
import type { OpeningProgressProps, ProgressMode } from "#/types";

const props = defineProps<OpeningProgressProps>();
const active = computed(() => props.progress.active);
const mode = computed<ProgressMode>(() =>
  props.progress.planned > 0 ? "determinate" : "indeterminate",
);
const value = computed(() => {
  const { opened, planned } = props.progress;

  return planned > 0 ? (opened / planned) * 100 : undefined;
});
const text = computed(() => {
  if (props.progress.planned === 0) {
    return getPlainMessage("pendingSearching");
  }

  return getMessage("openingProgress", {
    opened: props.progress.opened,
    planned: props.progress.planned,
  });
});
const showValue = false;
</script>

<template>
  <div v-if="active" id="opening-progress" class="mt-3">
    <ProgressBar :mode="mode" :value="value" :show-value="showValue" :aria-label="text" />
    <p class="mt-1 text-muted-color" aria-live="polite">{{ text }}</p>
  </div>
</template>
