<script setup lang="ts">
import Button from "primevue/button";
import Menu from "primevue/menu";
import { computed, nextTick, ref } from "vue";
import { useActionMenu } from "#/composables/useActionMenu";
import ActionIcon from "./ActionIcon.vue";

interface Action {
  id: string;
  label: string;
  hint: string;
  icon: "open" | "review" | "merge" | "copy";
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
    icon: action.icon,
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

function iconOf(icon: unknown): Action["icon"] {
  return icon === "open" || icon === "review" || icon === "merge" || icon === "copy"
    ? icon
    : "copy";
}
</script>

<template>
  <Button
    id="actions-menu"
    severity="secondary"
    text
    size="small"
    :aria-label="label"
    :aria-expanded="open"
    @click="handleToggle"
  >
    <ActionIcon name="menu" />
  </Button>
  <Menu
    ref="menu"
    popup
    append-to="self"
    :model="model"
    class="origin-top-right"
    @hide="handleHide"
    @keydown.esc="handleEscape"
  >
    <template #item="{ item, props: itemProps }">
      <a v-bind="itemProps.action" :aria-disabled="Boolean(item.disabled)">
        <ActionIcon :name="iconOf(item.icon)" />
        <span>{{ item.label }}</span>
      </a>
    </template>
  </Menu>
</template>
