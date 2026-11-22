<script setup lang="ts">
import { nextTick, useTemplateRef } from "vue";
import { useMessages } from "#/composables/useMessages";
import PrInput from "./PrInput.vue";
import RepoSelect from "./RepoSelect.vue";
import type { GoToFormEmits, GoToFormProps, RepoName } from "#/types";

defineProps<GoToFormProps>();

const { goToLabel } = useMessages(["goToLabel"]);

const input = defineModel<string>("input", { required: true });
const repo = defineModel<RepoName>("repo", { required: true });
const emit = defineEmits<GoToFormEmits>();
const prInput = useTemplateRef<{ focus: () => void }>("prInput");

function handleFormSubmit(): void {
  emit("go");
}

function handleRepoPick(): void {
  emit("pick");
}

function handleRepoClose(): void {
  emit("closeRepo");
  void nextTick(() => prInput.value?.focus());
}

function handleRepoAccept(): void {
  emit("acceptRepo");
}
</script>

<template>
  <form id="go" @submit.prevent="handleFormSubmit">
    <div class="mb-1.5 flex items-center justify-between">
      <label for="target" class="font-label">{{ goToLabel }}</label>
      <slot name="actions" />
    </div>
    <PrInput ref="prInput" v-model="input" :readonly="busy" />
    <RepoSelect
      v-if="showRepo"
      v-model="repo"
      :repos="repos"
      :guess-unavailable="guessUnavailable"
      open
      @accept="handleRepoAccept"
      @close="handleRepoClose"
      @pick="handleRepoPick"
    />
  </form>
</template>
