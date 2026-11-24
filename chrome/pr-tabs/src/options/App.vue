<script setup lang="ts">
import Button from "primevue/button";
import InputText from "primevue/inputtext";
import ToggleSwitch from "primevue/toggleswitch";
import { computed } from "vue";
import { useFileSettings } from "#/composables/useFileSettings";
import { getMessage } from "#/i18n";
import PatternItem from "./PatternItem.vue";
const {
  enabled,
  patterns,
  pattern,
  disabled,
  failed,
  feedback,
  invalid,
  handleToggle,
  handleAdd,
  remove,
} = useFileSettings();
const title = getMessage("settingsPageTitle");
const sectionTitle = getMessage("hideFilesTitle");
const toggleLabel = getMessage("hideFilesEnabled");
const help = getMessage("hideFilesHelp");
const patternsLabel = getMessage("filePatternsLabel");
const patternLabel = getMessage("filePattern");
const placeholder = getMessage("filePatternPlaceholder");
const addLabel = getMessage("addPattern");
const errorLabel = getMessage("fileSettingsError");
const iconLabel = getMessage("extName");
const iconUrl = "./icons/icon-48.png";
const patternClass = computed(() => ({ "patterns-inactive": !enabled.value }));
function handleRemove(value: string): void {
  remove(value);
}
</script>
<template>
  <main id="file-settings">
    <header class="settings-header flex items-center gap-3">
      <img :src="iconUrl" :alt="iconLabel" class="settings-icon" />
      <h1>{{ title }}</h1>
    </header>
    <section class="settings-card" aria-labelledby="hide-files-title">
      <h2 id="hide-files-title">{{ sectionTitle }}</h2>
      <p class="settings-help">{{ help }}</p>
      <fieldset :disabled="disabled">
        <div class="settings-toggle flex items-center justify-between gap-3">
          <label for="hide-matching-files">{{ toggleLabel }}</label>
          <ToggleSwitch
            input-id="hide-matching-files"
            :model-value="enabled"
            :disabled="disabled"
            @update:model-value="handleToggle"
          />
        </div>
        <div class="settings-patterns" :class="patternClass">
          <h3 id="patterns-label">{{ patternsLabel }}</h3>
          <ul aria-labelledby="patterns-label">
            <PatternItem
              v-for="pattern in patterns"
              :key="pattern"
              :pattern="pattern"
              :disabled="disabled"
              @remove="handleRemove"
            />
          </ul>
        </div>
        <form @submit.prevent="handleAdd" class="settings-add-form">
          <label for="file-pattern">{{ patternLabel }}</label>
          <div class="settings-add-row flex gap-2">
            <InputText
              id="file-pattern"
              v-model="pattern"
              :placeholder="placeholder"
              :disabled="disabled"
              :invalid="invalid"
              aria-describedby="pattern-feedback"
            />
            <Button
              type="submit"
              size="small"
              icon="pi pi-plus"
              :label="addLabel"
              :disabled="disabled"
            />
          </div>
          <p id="pattern-feedback" class="settings-feedback" aria-live="polite">{{ feedback }}</p>
        </form>
      </fieldset>
      <p v-if="failed" class="settings-error" role="alert">{{ errorLabel }}</p>
    </section>
  </main>
</template>
