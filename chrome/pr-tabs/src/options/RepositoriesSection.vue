<script setup lang="ts">
import { computed } from "vue";
import Button from "primevue/button";
import InputText from "primevue/inputtext";
import { useRepositorySettings } from "#/composables/useRepositorySettings";
import { getMessage } from "#/i18n";
import RepositoryItem from "./RepositoryItem.vue";
const { entries, repository, feedback, busy, handleAdd, remove } = useRepositorySettings();
const invalid = computed(() => Boolean(feedback.value));
function handleRemove(repo: string): void {
  remove(repo);
}
const title = getMessage("repositoriesTitle");
const help = getMessage("repositoriesHelp");
const label = getMessage("repositoryLabel");
const placeholder = getMessage("repositoryPlaceholder");
const addLabel = getMessage("addRepository");
</script>
<template>
  <section class="settings-card" aria-labelledby="repositories-title">
    <h2 id="repositories-title">{{ title }}</h2>
    <p class="settings-help">{{ help }}</p>
    <fieldset :disabled="busy">
      <ul :aria-label="title" class="settings-repositories">
        <RepositoryItem
          v-for="(entry, repo) in entries"
          :key="repo"
          v-bind="entry"
          :disabled="busy"
          @remove="handleRemove"
        />
      </ul>
      <form class="settings-add-form" @submit.prevent="handleAdd">
        <label for="repository">{{ label }}</label>
        <div class="settings-add-row flex gap-2">
          <InputText
            id="repository"
            v-model="repository"
            :placeholder="placeholder"
            :disabled="busy"
            :invalid="invalid"
            aria-describedby="repository-feedback"
          />
          <Button type="submit" size="small" icon="pi pi-plus" :label="addLabel" :disabled="busy" />
        </div>
        <p id="repository-feedback" class="settings-feedback" aria-live="polite">{{ feedback }}</p>
      </form>
    </fieldset>
  </section>
</template>
