<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useMessages } from "#/composables/useMessages";
import type { StatusToastProps } from "#/types";

const SUCCESS_MS = 3000;

const props = defineProps<StatusToastProps>();
const { closeStatus } = useMessages(["closeStatus"]);
const open = ref(false);
const role = computed(() => (props.error ? "alert" : "status"));
const toastClass = computed(() => ({
  "status-toast-error": props.error,
  "status-toast-raised": props.raised,
}));
const iconClass = computed(() => (props.error ? "pi-exclamation-circle" : "pi-check-circle"));
let timer: ReturnType<typeof setTimeout> | undefined;

function stopTimer(): void {
  clearTimeout(timer);
  timer = undefined;
}

function handleClose(): void {
  stopTimer();
  open.value = false;
}

function handleMessageChanged(): void {
  stopTimer();
  open.value = props.shown;

  if (props.shown && !props.error) {
    timer = setTimeout(handleClose, SUCCESS_MS);
  }
}

watch(() => [props.revision, props.text, props.error, props.shown], handleMessageChanged, {
  immediate: true,
});
onBeforeUnmount(stopTimer);
</script>

<template>
  <div v-if="open" id="status" class="status-toast" :class="toastClass" :role="role">
    <i class="status-toast-icon pi" :class="iconClass" aria-hidden="true" />
    <span class="status-toast-text">{{ text }}</span>
    <button
      type="button"
      class="status-toast-close"
      :aria-label="closeStatus"
      :title="closeStatus"
      @click="handleClose"
    >
      <i class="pi pi-times" aria-hidden="true" />
    </button>
  </div>
</template>
