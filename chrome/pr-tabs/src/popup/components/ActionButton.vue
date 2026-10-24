<script setup lang="ts">
import Button from "primevue/button";
import { computed } from "vue";
import type { ActionButtonEmits, ActionButtonProps } from "#/types";

const props = defineProps<ActionButtonProps>();
const emit = defineEmits<ActionButtonEmits>();
const spacingClass = computed(() => (props.primary ? "mt-3" : "mt-2"));
const severity = computed(() => (props.primary ? undefined : "secondary"));

function handleButtonClick(): void {
  emit("run");
}
</script>

<template>
  <Button
    class="w-full"
    :class="spacingClass"
    size="small"
    :severity="severity"
    :label="label"
    :disabled="busy"
    @click="handleButtonClick"
  />
</template>
