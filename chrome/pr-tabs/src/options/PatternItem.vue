<script setup lang="ts">
import { getMessage } from "#/i18n";
const props = defineProps<{ pattern: string }>();
const emit = defineEmits<{ remove: [pattern: string] }>();
const removeLabel = getMessage("removePattern");
function handleRemove(): void {
  emit("remove", props.pattern);
}
</script>
<template>
  <li>
    <code>{{ pattern }}</code>
    <button type="button" @click="handleRemove">{{ removeLabel }}</button>
  </li>
</template>
