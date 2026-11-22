<script setup lang="ts">
import { computed } from "vue";
import { groupColorClass } from "#/composables/useGroupItem";
import { useMessages } from "#/composables/useMessages";
import type { RepoOptionEvents, RepoOptionProps } from "#/types";

const props = defineProps<RepoOptionProps>();
const emit = defineEmits<RepoOptionEvents>();
const { alreadyOpen } = useMessages(["alreadyOpen"]);
const id = computed(() => props.option.id);
const repo = computed(() => props.option.repo);
const isOpen = computed(() => props.option.open !== null);
const isSelected = computed(() => props.selected === props.option.id);
const rowClass = computed(() => ({ "repo-option-selected": isSelected.value }));
const barClass = computed(() => groupColorClass(props.option.color));

function handleOptionClick(): void {
  emit("pick", props.option);
}

function handleOptionMouseenter(): void {
  emit("hover", props.option);
}
</script>

<template>
  <div
    :id="id"
    class="repo-option"
    :class="rowClass"
    role="option"
    :aria-selected="isSelected"
    @click="handleOptionClick"
    @mouseenter="handleOptionMouseenter"
  >
    <span class="repo-option-bar" :class="barClass" />
    <span class="repo-option-name">{{ repo }}</span>
    <span v-if="isOpen" class="repo-option-hint">{{ alreadyOpen }}</span>
  </div>
</template>
