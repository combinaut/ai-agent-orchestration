<script setup lang="ts">
import Select from "primevue/select";
import { nextTick, onBeforeUnmount, onMounted, useTemplateRef, watch } from "vue";
import { useMessages } from "#/composables/useMessages";
import type { RepoName, RepoSelectEmits, RepoSelectProps } from "#/types";

const props = defineProps<RepoSelectProps>();

const {
  repoSelectLabel,
  repoSelectedMessage,
  repoNoneSelectedMessage,
  repoEmptyMessage,
  repoGuessUnavailable,
} = useMessages([
  "repoSelectLabel",
  "repoSelectedMessage",
  "repoNoneSelectedMessage",
  "repoEmptyMessage",
  "repoGuessUnavailable",
]);

const repo = defineModel<RepoName>({ required: true });
const emit = defineEmits<RepoSelectEmits>();
const select = useTemplateRef<{ show: () => void }>("select");

function show(): void {
  select.value?.show();
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      void nextTick(show);
    }
  },
  { immediate: true },
);

function handleRepoChange(): void {
  emit("pick");
}

function handleEscape(): void {
  emit("close");
}

function handleEnter(): void {
  emit("accept");
}

function captureEscape(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopImmediatePropagation();
    emit("close");
  }
}

onMounted(() => document.addEventListener("keydown", captureEscape, true));
onBeforeUnmount(() => document.removeEventListener("keydown", captureEscape, true));
</script>

<template>
  <Select
    id="repo"
    ref="select"
    v-model="repo"
    :options="repos"
    size="small"
    class="mt-2 w-full"
    :aria-label="repoSelectLabel"
    :selection-message="repoSelectedMessage"
    :empty-selection-message="repoNoneSelectedMessage"
    :empty-message="repoEmptyMessage"
    @change="handleRepoChange"
    @keydown.enter.prevent.stop="handleEnter"
    @keydown.esc.stop="handleEscape"
  />
  <small v-if="guessUnavailable" class="mt-1 block text-muted-color">
    {{ repoGuessUnavailable }}
  </small>
</template>
