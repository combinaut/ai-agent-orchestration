<script setup lang="ts">
import IconField from "primevue/iconfield";
import InputIcon from "primevue/inputicon";
import InputText from "primevue/inputtext";
import { computed, onMounted, useTemplateRef } from "vue";
import SearchIcon from "./SearchIcon.vue";

const props = defineProps<{ mode: "idle" | "goto" | "search"; matches: number; busy: boolean }>();
const emit = defineEmits<{ go: []; next: []; clear: [] }>();
const value = defineModel<string>({ required: true });
const input = useTemplateRef<{ $el: HTMLInputElement }>("input");
const trailing = computed(() => {
  if (props.mode === "goto") {
    return "PR number";
  }

  return props.mode === "search" ? `${props.matches} matches` : "";
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
  } else if (event.key === "Escape" && value.value) {
    event.preventDefault();
    event.stopPropagation();
    emit("clear");
  }
}

defineExpose({ focus });
onMounted(focus);
</script>

<template>
  <IconField class="popup-field mx-3 mt-2">
    <InputIcon class="popup-search-icon"><SearchIcon /></InputIcon>
    <InputText
      id="target"
      ref="input"
      v-model="value"
      class="w-full"
      autocomplete="off"
      spellcheck="false"
      role="combobox"
      aria-controls="popup-list"
      :aria-expanded="mode === 'goto'"
      :placeholder="'PR number, link, or search open tabs'"
      :readonly="busy"
      @keydown="handleKeydown"
    />
    <InputIcon v-if="trailing" class="text-[12px] text-[var(--muted)]">{{ trailing }}</InputIcon>
  </IconField>
</template>
