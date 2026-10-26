<script setup lang="ts">
import { computed } from "vue";
import type { TextSegmentProps } from "#/types";

const props = defineProps<TextSegmentProps>();
const text = computed(() => props.segment.text);
const matched = computed(() => props.segment.matched);
</script>

<template>
  <mark v-if="matched" class="bg-search-match font-semibold text-search-match-text">
    {{ text }}
  </mark>
  <template v-else>{{ text }}</template>
</template>
