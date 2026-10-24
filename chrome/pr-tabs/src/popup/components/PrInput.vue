<script setup lang="ts">
import InputGroup from "primevue/inputgroup";
import InputGroupAddon from "primevue/inputgroupaddon";
import InputText from "primevue/inputtext";
import { useFocus } from "@vueuse/core";
import { useTemplateRef } from "vue";
import SearchIcon from "./SearchIcon.vue";

defineProps<{ readonly: boolean }>();

const text = defineModel<string>({ required: true });

useFocus(useTemplateRef("field"), { initialValue: true });
</script>

<template>
  <InputGroup>
    <InputGroupAddon>
      <SearchIcon />
    </InputGroupAddon>
    <InputText
      id="target"
      ref="field"
      v-model="text"
      size="small"
      autofocus
      autocomplete="off"
      spellcheck="false"
      placeholder="2972 or a PR URL"
      aria-describedby="target-hint"
      :readonly="readonly"
    />
    <InputGroupAddon>
      <kbd id="target-hint" class="font-sans text-hint" aria-label="Press Enter">↵ Enter</kbd>
    </InputGroupAddon>
  </InputGroup>
</template>
