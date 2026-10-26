<script setup lang="ts">
import Button from "primevue/button";
import { computed } from "vue";
import type { ActionButtonEmits, ActionButtonProps } from "#/types";

const props = defineProps<ActionButtonProps>();
const emit = defineEmits<ActionButtonEmits>();
const severity = computed(() => (props.primary ? undefined : "secondary"));

function handleButtonClick(): void {
  emit("run");
}
</script>

<template>
  <Button
    class="min-w-0 flex-1"
    size="small"
    :severity="severity"
    :label="label"
    :aria-label="hint"
    :title="hint"
    :disabled="busy"
    @click="handleButtonClick"
  />
</template>
