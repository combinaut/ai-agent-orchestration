<script setup lang="ts">
import Accordion from "primevue/accordion";
import { computed, ref, watch, useTemplateRef } from "vue";
import { lookupHeads } from "#/core";
import { useMessages } from "#/composables/useMessages";
import type { ComponentPublicInstance } from "vue";
import type { GroupListProps, GroupRows, PrKey, TabId, PrRowEvents } from "#/types";
import GroupItem from "./GroupItem.vue";

const props = defineProps<GroupListProps>();
const emit = defineEmits<PrRowEvents>();
const { groupsLabel } = useMessages(["groupsLabel"]);
const expanded = ref<string[]>([]);
const filteredExpanded = ref<string[]>([]);
const list = useTemplateRef<ComponentPublicInstance>("list");

const accordionValue = computed({ get: readExpanded, set: handleExpandedSet });
const groupRows = computed<GroupRows>(() =>
  Object.fromEntries(props.groups.map((group) => [`group-${group.id}`, group])),
);

function readExpanded(): string[] {
  return props.filtering ? filteredExpanded.value : expanded.value;
}

function handleExpandedSet(value: string[]): void {
  if (props.filtering) {
    filteredExpanded.value = value;
  } else {
    expanded.value = value;
  }
}

function handleGroupsUpdate(): void {
  void props.panes.ensure(lookupHeads(props.groups));
}

function handleGroupsChanged(): void {
  if (props.filtering) {
    filteredExpanded.value = props.groups.map((group) => group.id);
  }

  if (props.filtering || expanded.value.length > 0) {
    handleGroupsUpdate();
  }
}

function handlePrGo(key: PrKey): void {
  emit("go", key);
}

function handlePrClose(key: PrKey, tabId: TabId): void {
  emit("close", key, tabId);
}

function focusFirst(): void {
  const root: unknown = list.value?.$el;
  if (root instanceof HTMLElement) {
    root.querySelector<HTMLButtonElement>("button[data-pr-tab]")?.focus();
  }
}

watch([() => props.groups, () => props.filtering], handleGroupsChanged, { immediate: true });

defineExpose({ focusFirst });
</script>

<template>
  <Accordion
    id="groups"
    ref="list"
    v-model:value="accordionValue"
    class="mt-2"
    multiple
    :aria-label="groupsLabel"
    @update:value="handleGroupsUpdate"
  >
    <GroupItem
      v-for="(group, id) in groupRows"
      :key="id"
      :group="group"
      :panes="panes"
      @go="handlePrGo"
      @close="handlePrClose"
    />
  </Accordion>
</template>
