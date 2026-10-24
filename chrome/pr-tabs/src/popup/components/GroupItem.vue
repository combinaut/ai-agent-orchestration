<script setup lang="ts">
import Badge from "primevue/badge";
import type { GroupItemProps } from "#/types";
import { useGroupItem } from "#/composables/useGroupItem";

const props = defineProps<GroupItemProps>();
const { title, count, dotClass, countLabel } = useGroupItem(() => props.group);
</script>

<template>
  <li class="flex items-center gap-2 py-group-row">
    <span
      class="size-dot flex-none rounded-full shadow-dot-ring"
      :class="dotClass"
      aria-hidden="true"
    />
    <span>{{ title }}</span>
    <Badge
      class="ml-auto tabular-nums"
      severity="secondary"
      :value="count"
      :aria-label="countLabel"
    />
  </li>
</template>
