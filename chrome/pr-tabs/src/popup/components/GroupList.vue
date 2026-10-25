<script setup lang="ts">
import { computed } from "vue";
import { useMessages } from "#/composables/useMessages";
import type { GroupListProps } from "#/types";
import GroupItem from "./GroupItem.vue";

const props = defineProps<GroupListProps>();
const { groupsLabel } = useMessages(["groupsLabel"]);
const groupRows = computed(() => props.groups);
</script>

<template>
  <ul id="groups" class="mt-2 list-none p-0" :aria-label="groupsLabel">
    <GroupItem v-for="(group, index) in groupRows" :key="index" :group="group" />
  </ul>
</template>
