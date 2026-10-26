<script setup lang="ts">
import IconField from "primevue/iconfield";
import InputIcon from "primevue/inputicon";
import InputText from "primevue/inputtext";
import { computed } from "vue";
import { getPlainMessage, getPluralMessage } from "#/i18n";
import type { PrSearchCommands, PrSearchProps } from "#/types";
import SearchIcon from "./SearchIcon.vue";

const props = defineProps<PrSearchProps>();
const emit = defineEmits<PrSearchCommands>();
const query = defineModel<string>({ required: true });
const label = getPlainMessage("prSearchLabel");
const resultText = computed(() => {
  if (!props.active) {
    return "";
  }

  return props.count === 0
    ? getPlainMessage("prSearchEmpty")
    : getPluralMessage("prSearchCount", props.count);
});

function handleSearchKeydown(event: KeyboardEvent): void {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    emit("next");
  } else if (event.key === "Enter") {
    event.preventDefault();
    emit("go");
  } else if (event.key === "Escape" && query.value.length > 0) {
    event.preventDefault();
    event.stopPropagation();
    query.value = "";
  }
}
</script>

<template>
  <div class="mt-3">
    <IconField>
      <InputIcon><SearchIcon /></InputIcon>
      <InputText
        id="pr-search"
        v-model="query"
        class="w-full"
        size="small"
        autocomplete="off"
        spellcheck="false"
        :aria-label="label"
        :placeholder="label"
        @keydown="handleSearchKeydown"
      />
    </IconField>
    <p class="mt-1 text-hint text-muted-color" aria-live="polite">{{ resultText }}</p>
  </div>
</template>
