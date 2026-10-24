<script setup lang="ts">
import Select from "primevue/select";

defineProps<{ repos: string[] }>();

const repo = defineModel<string>({ required: true });
const emit = defineEmits<{ pick: [] }>();

function handleRepoChange() {
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
