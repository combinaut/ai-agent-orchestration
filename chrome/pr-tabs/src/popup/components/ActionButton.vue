<script setup lang="ts">
import Button from "primevue/button";
import { computed } from "vue";

const props = defineProps<{ label: string; busy: boolean; primary?: boolean }>();
const emit = defineEmits<{ run: [] }>();
const spacingClass = computed(() => (props.primary ? "mt-3" : "mt-2"));
const severity = computed(() => (props.primary ? undefined : "secondary"));

function handleButtonClick() {
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
