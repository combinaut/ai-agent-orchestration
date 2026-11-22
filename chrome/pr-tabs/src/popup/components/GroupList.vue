<script setup lang="ts">
import Accordion from "primevue/accordion";
import { computed, watch } from "vue";
import { lookupHeads } from "#/core";
import { useMessages } from "#/composables/useMessages";
import type { GroupEvents, GroupListProps, GroupRows, PrKey, TabId } from "#/types";
import GroupItem from "./GroupItem.vue";

const props = defineProps<GroupListProps>();
const emit = defineEmits<GroupEvents>();
const { groupsLabel } = useMessages(["groupsLabel"]);
const groupRows = computed<GroupRows>(() =>
  Object.fromEntries(props.groups.map((group) => [`group-${group.id}`, group])),
);
const expanded = computed(() =>
  props.groups.map((group) => group.id).filter((id) => !props.collapsed.has(id)),
);

function isArray(value: unknown): value is unknown[] {
  return Array.isArray(value);
}

function handleExpandedUpdate(value: unknown): void {
  const open = new Set(isArray(value) ? value.map(String) : []);

  for (const group of props.groups) {
    const wasOpen = !props.collapsed.has(group.id);

    if (wasOpen !== open.has(group.id)) {
      emit("collapse", group.id, wasOpen);
    }
  }
}

function handleCollapse(id: string, collapsed: boolean): void {
  emit("collapse", id, collapsed);
}

function handlePrGo(key: PrKey): void {
  emit("go", key);
}

function handlePrClose(key: PrKey, tabId: TabId): void {
  emit("close", key, tabId);
}

function handleGroupsChanged(): void {
  void props.panes.ensure(lookupHeads(props.groups));
}

watch(() => props.groups, handleGroupsChanged, { immediate: true });
</script>

<template>
  <Accordion
    id="groups"
    class="group-list"
    multiple
    :value="expanded"
    :aria-label="groupsLabel"
    @update:value="handleExpandedUpdate"
  >
    <GroupItem
      v-for="(group, id) in groupRows"
      :key="id"
      :group="group"
      :panes="panes"
      :collapsed="collapsed"
      :selected="selected"
      @collapse="handleCollapse"
      @go="handlePrGo"
      @close="handlePrClose"
    />
  </Accordion>
</template>
