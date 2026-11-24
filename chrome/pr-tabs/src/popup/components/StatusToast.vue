<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useMessages } from "#/composables/useMessages";
import type { StatusKind, StatusToastProps } from "#/types";

const HIDE_MS = 4000;
const ICONS: Readonly<Record<StatusKind, string>> = {
  success: "pi-check-circle",
  info: "pi-info-circle",
  warning: "pi-exclamation-triangle",
  error: "pi-times-circle",
};

const props = defineProps<StatusToastProps>();
const { closeStatus } = useMessages(["closeStatus"]);
const open = ref(false);
const hovered = ref(false);
const focused = ref(false);
const hides = computed(() => props.kind === "success" || props.kind === "info");
const role = computed(() => (hides.value ? "status" : "alert"));
const toastClass = computed(() => [
  `status-toast-${props.kind}`,
  { "status-toast-raised": props.raised },
]);
const iconClass = computed(() => ICONS[props.kind]);
let timer: ReturnType<typeof setTimeout> | undefined;

function stopTimer(): void {
  clearTimeout(timer);
  timer = undefined;
}

function startTimer(): void {
  stopTimer();

  if (open.value && hides.value && !hovered.value && !focused.value) {
    timer = setTimeout(handleClose, HIDE_MS);
  }
}

function handleClose(): void {
  stopTimer();
  open.value = false;
  hovered.value = false;
  focused.value = false;
}

function handleMessageChanged(): void {
  open.value = props.shown;

  if (!props.shown) {
    hovered.value = false;
    focused.value = false;
  }

  startTimer();
}

function handleToastMouseenter(): void {
  hovered.value = true;
  startTimer();
}

function handleToastMouseleave(): void {
  hovered.value = false;
  startTimer();
}

function handleToastFocusin(): void {
  focused.value = true;
  startTimer();
}

function handleToastFocusout(): void {
  focused.value = false;
  startTimer();
}

watch(() => [props.revision, props.text, props.kind, props.shown], handleMessageChanged, {
  immediate: true,
});
onBeforeUnmount(stopTimer);
</script>

<template>
  <Transition name="status-toast">
    <div
      v-if="open"
      id="status"
      class="status-toast"
      :class="toastClass"
      :role="role"
      @mouseenter="handleToastMouseenter"
      @mouseleave="handleToastMouseleave"
      @focusin="handleToastFocusin"
      @focusout="handleToastFocusout"
    >
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
  </Transition>
</template>
