<script setup lang="ts">
import Select from "primevue/select";
import { useMessages } from "#/composables/useMessages";
import type { RepoName, RepoSelectEmits, RepoSelectProps } from "#/types";

defineProps<RepoSelectProps>();

const { repoSelectLabel, repoSelectedMessage, repoNoneSelectedMessage, repoEmptyMessage } =
  useMessages([
    "repoSelectLabel",
    "repoSelectedMessage",
    "repoNoneSelectedMessage",
    "repoEmptyMessage",
  ]);

const repo = defineModel<RepoName>({ required: true });
const emit = defineEmits<RepoSelectEmits>();

function handleRepoChange(): void {
  emit("pick");
}
</script>

<template>
  <Select
    id="repo"
    v-model="repo"
    :options="repos"
    size="small"
    class="mt-2 w-full"
    :aria-label="repoSelectLabel"
    :selection-message="repoSelectedMessage"
    :empty-selection-message="repoNoneSelectedMessage"
    :empty-message="repoEmptyMessage"
    @change="handleRepoChange"
  />
</template>
