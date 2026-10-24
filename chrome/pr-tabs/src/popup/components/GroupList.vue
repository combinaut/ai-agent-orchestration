<script setup lang="ts">
import type { GroupListProps } from "#/types";
import GroupItem from "./GroupItem.vue";

defineProps<GroupListProps>();
</script>

<template>
  <ul id="groups" class="mt-2 list-none p-0" aria-label="PR tab groups">
    <GroupItem v-for="(group, index) in groups" :key="index" :group="group" />
  </ul>
</template>
