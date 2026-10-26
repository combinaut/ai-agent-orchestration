<script setup lang="ts">
import Accordion from "primevue/accordion";
import { computed, ref, watch } from "vue";
import { lookupHeads } from "#/core";
import { useMessages } from "#/composables/useMessages";
import type { GroupListProps, GroupRows, PrKey } from "#/types";
import GroupItem from "./GroupItem.vue";

const props = defineProps<GroupListProps>();
const emit = defineEmits<{ go: [key: PrKey] }>();
const { groupsLabel } = useMessages(["groupsLabel"]);
const expanded = ref<string[]>([]);
const groupRows = computed<GroupRows>(() =>
  Object.fromEntries(props.groups.map((group) => [`group-${group.id}`, group])),
);

function handleGroupsUpdate(): void {
  void props.panes.ensure(lookupHeads(props.groups));
}
function handleGroupsChanged(): void {
  if (expanded.value.length > 0) {
    handleGroupsUpdate();
  }
}

watch(() => props.groups, handleGroupsChanged);
function handlePrGo(key: PrKey): void {
  emit("go", key);
}
</script>

<template>
  <Accordion
    id="groups"
    v-model:value="expanded"
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
    />
  </Accordion>
</template>
