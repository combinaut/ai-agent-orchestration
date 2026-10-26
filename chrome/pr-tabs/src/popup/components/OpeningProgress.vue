<script setup lang="ts">
import ProgressBar from "primevue/progressbar";
import { computed } from "vue";
import { getMessage } from "#/i18n";
import type { OpeningProgressProps } from "#/types";

const props = defineProps<OpeningProgressProps>();
const active = computed(() => props.progress.active);
const value = computed(() => {
  const { opened, planned } = props.progress;

  return planned > 0 ? (opened / planned) * 100 : 0;
});
const text = computed(() =>
  getMessage("openingProgress", {
    opened: props.progress.opened,
    planned: props.progress.planned,
  }),
);
const showValue = false;
</script>

<template>
  <div v-if="active" id="opening-progress" class="mt-3">
    <ProgressBar :value="value" :show-value="showValue" :aria-label="text" />
    <p class="mt-1 text-muted-color" aria-live="polite">{{ text }}</p>
  </div>
</template>
