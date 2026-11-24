<script setup lang="ts">
import { useFileSettings } from "#/composables/useFileSettings";
import { getMessage } from "#/i18n";
import PatternItem from "./PatternItem.vue";
const { enabled, patterns, pattern, disabled, failed, handleToggle, handleAdd, remove } =
  useFileSettings();
const title = getMessage("hideFilesTitle");
const toggleLabel = getMessage("hideFilesEnabled");
const help = getMessage("hideFilesHelp");
const patternLabel = getMessage("filePattern");
const addLabel = getMessage("addPattern");
const errorLabel = getMessage("fileSettingsError");
function handleRemove(value: string): void {
  remove(value);
}
</script>
<template>
  <main id="file-settings">
    <h1>{{ title }}</h1>
    <fieldset :disabled="disabled">
      <label>
        <input type="checkbox" :checked="enabled" @change="handleToggle" />
        {{ toggleLabel }}
      </label>
      <p>{{ help }}</p>
      <ul>
        <PatternItem
          v-for="pattern in patterns"
          :key="pattern"
          :pattern="pattern"
          @remove="handleRemove"
        />
      </ul>
      <form @submit.prevent="handleAdd">
        <label for="file-pattern">{{ patternLabel }}</label>
        <input id="file-pattern" v-model="pattern" />
        <button type="submit">{{ addLabel }}</button>
      </form>
    </fieldset>
    <p v-if="failed" role="alert">{{ errorLabel }}</p>
  </main>
</template>
