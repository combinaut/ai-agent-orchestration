<script setup lang="ts">
import IconField from "primevue/iconfield";
import InputIcon from "primevue/inputicon";
import InputText from "primevue/inputtext";
import { computed, onMounted, useTemplateRef } from "vue";
import { getPlainMessage, getPluralMessage } from "#/i18n";
import type { PopupFieldEmits, PopupFieldProps } from "#/types";

const props = defineProps<PopupFieldProps>();
const emit = defineEmits<PopupFieldEmits>();
const value = defineModel<string>({ required: true });
const input = useTemplateRef<{ $el: HTMLInputElement }>("input");
const placeholder = getPlainMessage("fieldPlaceholder");
const fieldClass = computed(() => ({ "popup-field-goto": props.mode === "goto" }));
const activeDescendant = computed(() => props.active ?? undefined);
const trailing = computed(() => {
  if (props.mode === "goto") {
    return getPlainMessage("fieldNumberHint");
  }

  return props.mode === "search" ? getPluralMessage("fieldMatches", props.matches) : "";
});

function focus(): void {
  input.value?.$el.focus();
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.isComposing) {
    return;
  }

  if (event.key === "Enter") {
    event.preventDefault();
    emit("go");
  } else if (event.key === "ArrowDown") {
    event.preventDefault();
    emit("next");
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    emit("previous");
  } else if (event.key === "Escape" && value.value) {
    event.preventDefault();
    event.stopPropagation();
    emit("clear");
  }
}

onMounted(focus);
</script>

<template>
  <IconField class="popup-field" :class="fieldClass">
    <InputIcon class="pi pi-search" />
    <InputText
      id="target"
      ref="input"
      v-model="value"
      autocomplete="off"
      spellcheck="false"
      role="combobox"
      aria-controls="popup-list"
      :aria-expanded="expanded"
      :aria-activedescendant="activeDescendant"
      :placeholder="placeholder"
      :aria-label="placeholder"
      :readonly="busy"
      @keydown="handleKeydown"
    />
    <span v-show="trailing" class="popup-field-trailing" aria-live="polite">{{ trailing }}</span>
  </IconField>
</template>
