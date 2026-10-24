<script setup lang="ts">
import { computed } from "vue";
import type { GroupListProps } from "#/types";
import GroupItem from "./GroupItem.vue";

const props = defineProps<GroupListProps>();
const groupRows = computed(() => props.groups);
</script>

<template>
  <ul id="groups" class="mt-2 list-none p-0" aria-label="PR tab groups">
    <GroupItem v-for="(group, index) in groupRows" :key="index" :group="group" />
  </ul>
</template>
