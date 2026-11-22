<script setup lang="ts">
import { computed } from "vue";
import { ACTION_ICON_PATHS, type ActionIconName } from "#popup/icons";

const props = defineProps<{ name: ActionIconName }>();
const path = computed(() => ACTION_ICON_PATHS[props.name]);
</script>

<template>
  <svg class="size-4 fill-current" viewBox="0 0 16 16" aria-hidden="true">
    <path :d="path" />
  </svg>
</template>
