<script setup lang="ts">
import Button from "primevue/button";
import Menu from "primevue/menu";
import { computed, nextTick, ref } from "vue";
import { useActionMenu } from "#/composables/useActionMenu";

interface Action {
  id: string;
  label: string;
  hint: string;
  busy: boolean;
  run: () => void;
}

const props = defineProps<{ actions: Action[]; label: string }>();
const menu = ref<InstanceType<typeof Menu>>();
const { open, toggle, close } = useActionMenu();
const model = computed(() =>
  props.actions.map((action) => ({
    id: action.id,
    label: action.label,
    disabled: action.busy,
    command: action.run,
  })),
);

function handleToggle(event: Event): void {
  toggle();
  menu.value?.toggle(event);
}

function handleHide(): void {
  close();
}

function handleEscape(event: KeyboardEvent): void {
  event.preventDefault();
  event.stopPropagation();
  menu.value?.hide();
  close();
  void nextTick(() => document.getElementById("actions-menu")?.focus());
}
</script>

<template>
  <Button
    id="actions-menu"
    icon="pi pi-bars"
    severity="secondary"
    text
    size="small"
    :aria-label="label"
    :aria-expanded="open"
    @click="handleToggle"
  />
  <Menu
    ref="menu"
    popup
    append-to="self"
    :model="model"
    class="origin-top-right"
    @hide="handleHide"
    @keydown.esc="handleEscape"
  />
</template>
