<script setup lang="ts">
import Accordion from "primevue/accordion";
import { computed, ref, watch } from "vue";
import { lookupHeads } from "#/core";
import { useMessages } from "#/composables/useMessages";
import type { GroupListProps } from "#/types";
import GroupItem from "./GroupItem.vue";

const props = defineProps<GroupListProps>();
const { groupsLabel } = useMessages(["groupsLabel"]);
const expanded = ref<string[]>([]);
const groupRows = computed(() => props.groups);

function handleGroupsUpdate(): void {
  void props.panes.ensure(lookupHeads(props.groups));
}
function handleGroupsChanged(): void {
  if (expanded.value.length > 0) {
    handleGroupsUpdate();
  }
}

watch(() => props.groups, handleGroupsChanged);
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
      v-for="(group, index) in groupRows"
      :key="index"
      :group="group"
      :index="index"
      :panes="panes"
    />
  </Accordion>
</template>
