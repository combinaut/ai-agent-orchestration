<script setup lang="ts">
import IconField from "primevue/iconfield";
import InputIcon from "primevue/inputicon";
import InputText from "primevue/inputtext";
import { useFocus } from "@vueuse/core";
import { computed, useTemplateRef } from "vue";
import SearchIcon from "./SearchIcon.vue";
import type { PrInputProps } from "#/types";

defineProps<PrInputProps>();

const text = defineModel<string>({ required: true });
const isSubmitHintVisible = computed(() => text.value.trim() !== "");

useFocus(useTemplateRef("field"), { initialValue: true });
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
      placeholder="2972 or a PR URL"
      aria-describedby="target-hint"
      :readonly="readonly"
    />
    <InputIcon v-if="isSubmitHintVisible">
      <kbd id="target-hint" class="font-popup text-hint text-muted-color" aria-label="Press Enter"
        >↵</kbd
      >
    </InputIcon>
  </IconField>
</template>
