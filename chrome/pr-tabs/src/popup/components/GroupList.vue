<script setup lang="ts">
import type { GroupSummary } from "#/core";
import GroupItem from "./GroupItem.vue";

defineProps<{ groups: GroupSummary[] }>();
</script>

<template>
  <ul id="groups" class="mt-2 list-none p-0" aria-label="PR tab groups">
    <GroupItem v-for="(group, index) in groups" :key="index" :group="group" />
  </ul>
</template>
