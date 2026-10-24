<script setup lang="ts">
import Select from "primevue/select";
import type { RepoName, RepoSelectEmits, RepoSelectProps } from "#/types";

defineProps<RepoSelectProps>();

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
    aria-label="Repository for a bare number"
    @change="handleRepoChange"
  />
</template>
