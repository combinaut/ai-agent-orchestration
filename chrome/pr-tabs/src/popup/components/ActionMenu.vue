<script setup lang="ts">
import Menu from "primevue/menu";
import { computed, nextTick, ref } from "vue";
import { useActionMenu } from "#/composables/useActionMenu";
import type { ActionMenuItem } from "#/types";

const props = defineProps<{ actions: ActionMenuItem[]; label: string }>();
const menu = ref<InstanceType<typeof Menu>>();
const { open, toggle, close } = useActionMenu();
const buttonClass = computed(() => ({ "menu-button-open": open.value }));

interface MenuEntry {
  id?: string;
  label?: string;
  icon?: string;
  disabled?: boolean;
  separator?: boolean;
  command?: () => void;
}

function menuItem(action: ActionMenuItem): MenuEntry {
  return {
    id: action.id,
    label: action.label,
    icon: action.icon,
    disabled: action.busy,
    command: action.run,
  };
}

const model = computed<MenuEntry[]>(() =>
  props.actions.flatMap((action, index) =>
    action.separated && index > 0 ? [{ separator: true }, menuItem(action)] : [menuItem(action)],
  ),
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
  <button
    id="actions-menu"
    type="button"
    class="menu-button"
    :class="buttonClass"
    :aria-label="label"
    aria-haspopup="menu"
    aria-controls="actions-menu-popup"
    :aria-expanded="open"
    @click="handleToggle"
  >
    <i class="pi pi-bars" aria-hidden="true" />
  </button>
  <Menu
    id="actions-menu-popup"
    ref="menu"
    popup
    append-to="self"
    :model="model"
    class="action-menu-overlay"
    @hide="handleHide"
    @keydown.esc="handleEscape"
  >
    <template #item="{ item, props: itemProps }">
      <a v-bind="itemProps.action" :aria-disabled="Boolean(item.disabled)">
        <span class="menu-item-icon">
          <i class="pi" :class="item.icon" aria-hidden="true" />
        </span>
        <span>{{ item.label }}</span>
      </a>
    </template>
  </Menu>
</template>
