<script setup lang="ts">
import PrInput from "./PrInput.vue";
import RepoSelect from "./RepoSelect.vue";

defineProps<{ busy: boolean; repos: string[]; showRepo: boolean }>();

const input = defineModel<string>("input", { required: true });
const repo = defineModel<string>("repo", { required: true });
const emit = defineEmits<{ go: []; pick: [] }>();

function handleFormSubmit() {
  emit("go");
}

function handleRepoPick() {
  emit("pick");
}
</script>

<template>
  <form id="go" @submit.prevent="handleFormSubmit">
    <label for="target" class="mb-1.5 block font-semibold">Go to a PR</label>
    <PrInput v-model="input" :readonly="busy" />
    <RepoSelect v-if="showRepo" v-model="repo" :repos="repos" @pick="handleRepoPick" />
  </form>
</template>
