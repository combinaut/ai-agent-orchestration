<script setup lang="ts">
import Message from "primevue/message";

defineProps<{ text: string; error: boolean }>();
</script>

<template>
  <div id="status" class="mt-3 min-h-status" role="status" aria-live="polite">
    <Message v-if="error" severity="error" size="small">{{ text }}</Message>
    <p v-else class="m-0 text-muted-color">{{ text }}</p>
  </div>
</template>
