<script setup lang="ts">
import Button from "primevue/button";
import { computed } from "vue";
import { getMessage } from "#/i18n";
const props = defineProps<{ pattern: string; disabled: boolean }>();
const emit = defineEmits<{ remove: [pattern: string] }>();
const removeLabel = computed(() => getMessage("removePattern", { pattern: props.pattern }));
function handleRemove(): void {
  emit("remove", props.pattern);
}
</script>
<template>
  <li class="settings-pattern">
    <code>{{ pattern }}</code>
    <Button
      type="button"
      icon="pi pi-trash"
      severity="secondary"
      size="small"
      text
      rounded
      :aria-label="removeLabel"
      :disabled="disabled"
      @click="handleRemove"
    />
  </li>
</template>
