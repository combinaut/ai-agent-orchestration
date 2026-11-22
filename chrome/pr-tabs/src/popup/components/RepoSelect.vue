<script setup lang="ts">
import Select from "primevue/select";
import { nextTick, useTemplateRef, watch } from "vue";
import { useMessages } from "#/composables/useMessages";
import type { RepoName, RepoSelectEmits, RepoSelectProps } from "#/types";

const props = defineProps<RepoSelectProps>();

const { repoSelectLabel, repoSelectedMessage, repoNoneSelectedMessage, repoEmptyMessage } =
  useMessages([
    "repoSelectLabel",
    "repoSelectedMessage",
    "repoNoneSelectedMessage",
    "repoEmptyMessage",
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
</template>
