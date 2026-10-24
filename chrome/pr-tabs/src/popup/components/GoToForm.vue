<script setup lang="ts">
import PrInput from "./PrInput.vue";
import RepoSelect from "./RepoSelect.vue";
import type { GoToFormEmits, GoToFormProps, RepoName } from "#/types";

defineProps<GoToFormProps>();

const input = defineModel<string>("input", { required: true });
const repo = defineModel<RepoName>("repo", { required: true });
const emit = defineEmits<GoToFormEmits>();

function handleFormSubmit(): void {
  emit("go");
}

function handleRepoPick(): void {
  emit("pick");
}
</script>

<template>
  <form id="go" @submit.prevent="handleFormSubmit">
    <label for="target" class="mb-1.5 block font-label">Go to a PR</label>
    <PrInput v-model="input" :readonly="busy" />
    <RepoSelect v-if="showRepo" v-model="repo" :repos="repos" @pick="handleRepoPick" />
  </form>
</template>
