<script setup lang="ts">
import IconField from "primevue/iconfield";
import InputIcon from "primevue/inputicon";
import InputText from "primevue/inputtext";
import { computed, onMounted, useTemplateRef } from "vue";
import { useMessages } from "#/composables/useMessages";
import SearchIcon from "./SearchIcon.vue";
import type { PrInputProps } from "#/types";

defineProps<PrInputProps>();

const { goToPlaceholder, submitHintLabel } = useMessages(["goToPlaceholder", "submitHintLabel"]);

const text = defineModel<string>({ required: true });
const isSubmitHintVisible = computed(() => text.value.trim() !== "");

const field = useTemplateRef("field");

function focus(): void {
  const component = field.value;

  if (component && typeof component === "object" && "$el" in component) {
    const element = component.$el;

    if (element instanceof HTMLInputElement) {
      element.focus();
    }
  }
}

defineExpose({ focus });
onMounted(focus);
</script>

<template>
  <IconField>
    <InputIcon>
      <SearchIcon />
    </InputIcon>
    <InputText
      id="target"
      ref="field"
      v-model="text"
      class="w-full"
      size="small"
      autofocus
      autocomplete="off"
      spellcheck="false"
      :placeholder="goToPlaceholder"
      aria-describedby="target-hint"
      :readonly="readonly"
    />
    <InputIcon v-if="isSubmitHintVisible" class="text-hint text-muted-color">
      <kbd id="target-hint" class="font-popup" :aria-label="submitHintLabel">↵</kbd>
    </InputIcon>
  </IconField>
</template>
