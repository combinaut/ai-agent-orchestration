<script setup lang="ts">
import Button from "primevue/button";
import { computed } from "vue";
import { getMessage } from "#/i18n";
const props = defineProps<{ repo: string; configured: boolean; disabled: boolean }>();
const emit = defineEmits<{ remove: [repo: string] }>();
const configLabel = getMessage("repositoryFromConfig");
const removeLabel = computed(() => getMessage("removeRepository", { repo: props.repo }));
function handleRemove(): void {
  emit("remove", props.repo);
}
</script>
<template>
  <li class="settings-pattern">
    <code>{{ repo }}</code>
    <span v-if="configured" class="settings-help">{{ configLabel }}</span>
    <Button
      v-else
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
