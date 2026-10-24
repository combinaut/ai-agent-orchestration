<script setup lang="ts">
import Badge from "primevue/badge";
import Button from "primevue/button";
import InputGroup from "primevue/inputgroup";
import InputGroupAddon from "primevue/inputgroupaddon";
import InputText from "primevue/inputtext";
import Message from "primevue/message";
import Select from "primevue/select";
import { onMounted, ref, useTemplateRef } from "vue";
import { checkText, duplicateText, summaryText, type CheckResult, type GroupSummary, type Summary } from "../core";
import { ask as askWith, type Message as Request } from "../messages";

type Reply<T> = T | { error: string };

const DOTS: Record<string, string> = {
  grey: "bg-[#5f6368] dark:bg-[#dadce0]",
  blue: "bg-[#1a73e8] dark:bg-[#8ab4f8]",
  red: "bg-[#d93025] dark:bg-[#f28b82]",
  yellow: "bg-[#f9ab00] dark:bg-[#fdd663]",
  green: "bg-[#1e8e3e] dark:bg-[#81c995]",
  pink: "bg-[#d01884] dark:bg-[#ff8bcb]",
  purple: "bg-[#a142f4] dark:bg-[#d7aefb]",
  cyan: "bg-[#007b83] dark:bg-[#78d9ec]",
  orange: "bg-[#fa903e] dark:bg-[#fcad70]",
};

const ask = <T,>(message: Request) => askWith<T>((m) => chrome.runtime.sendMessage(m), message);
const failed = <T,>(reply: Reply<T>): reply is { error: string } => Boolean((reply as { error?: unknown }).error);

const status = ref({ text: "", error: false });
const groups = ref<GroupSummary[]>([]);
const input = ref("");
const readOnly = ref(false);
const repos = ref<string[]>([]);
const repo = ref("");
const showRepo = ref(false);
const opening = ref(false);
const checking = ref(false);
const closing = ref(false);
const target = useTemplateRef<{ $el: HTMLInputElement }>("target");

function show(text: string, isError = false) {
  status.value = { text, error: isError };
}

async function refresh() {
  const summary = await ask<Summary>({ type: "summary" });
  if (failed(summary)) {
    show(summary.error, true);
    return;
  }
  show(summaryText(summary));
  groups.value = summary.groups;
}

async function loadRepos() {
  const choices = await ask<{ repos: string[]; selected: string; show: boolean }>({ type: "repos" });
  if (failed(choices) || !choices.show) return;
  repos.value = choices.repos;
  repo.value = choices.selected;
  showRepo.value = true;
}

function pickRepo() {
  void ask({ type: "pickRepo", repo: repo.value });
}

async function go() {
  if (!input.value.trim() || readOnly.value) return;
  readOnly.value = true;
  const result = await ask({ type: "goTo", input: input.value, ...(showRepo.value && { repo: repo.value }) });
  readOnly.value = false;
  if (failed(result)) {
    show(result.error, true);
    return;
  }
  window.close();
}

async function openAll() {
  opening.value = true;
  show("Opening…");
  const result = await ask<{ opened: number; remaining: number; estimated: boolean }>({ type: "openAll" });
  opening.value = false;
  if (failed(result)) {
    show(result.error, true);
    return;
  }
  await refresh();
  const opened = `Opened ${result.opened}.`;
  const more = `${result.estimated ? "about " : ""}${result.remaining} more`;
  show(
    result.remaining
      ? `${opened} ${more} not opened; click again for the next batch.`
      : `${opened} ${status.value.text}`,
  );
}

async function checkNow() {
  checking.value = true;
  show("Checking…");
  const result = await ask<CheckResult>({ type: "checkNow" });
  checking.value = false;
  if (failed(result)) {
    show(result.error, true);
    return;
  }
  await refresh();
  show(checkText(result));
}

async function closeDuplicates() {
  closing.value = true;
  show("Closing…");
  const result = await ask<{ closed: number }>({ type: "closeDuplicates" });
  closing.value = false;
  if (failed(result)) {
    show(result.error, true);
    return;
  }
  await refresh();
  show(duplicateText(result.closed));
}

onMounted(() => {
  target.value?.$el.focus();
  void refresh();
  void loadRepos();
});
</script>

<template>
  <form id="go" @submit.prevent="go">
    <label for="target" class="mb-1.5 block font-semibold">Go to a PR</label>
    <InputGroup>
      <InputGroupAddon>
        <svg class="fill-current" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
          <path
            d="M10.68 11.74a6 6 0 1 1 1.06-1.06l3.04 3.04a.75.75 0 1 1-1.06 1.06ZM11.5 7a4.5 4.5 0 1 0-9 0 4.5 4.5 0 0 0 9 0Z"
          />
        </svg>
      </InputGroupAddon>
      <Select
        v-if="showRepo"
        id="repo"
        v-model="repo"
        :options="repos"
        size="small"
        class="max-w-[40%]"
        aria-label="Repository for a bare number"
        @change="pickRepo"
      />
      <InputText
        id="target"
        ref="target"
        v-model="input"
        size="small"
        autofocus
        autocomplete="off"
        spellcheck="false"
        placeholder="2972 or a PR URL"
        aria-describedby="target-hint"
        :readonly="readOnly"
      />
      <InputGroupAddon>
        <kbd id="target-hint" class="font-sans text-[11px]" aria-label="Press Enter">↵ Enter</kbd>
      </InputGroupAddon>
    </InputGroup>
  </form>
  <Button
    id="open-all"
    class="mt-3 w-full"
    size="small"
    label="Open all my open PRs"
    :disabled="opening"
    @click="openAll"
  />
  <Button
    id="check-now"
    class="mt-2 w-full"
    size="small"
    severity="secondary"
    label="Close merged PRs"
    :disabled="checking"
    @click="checkNow"
  />
  <Button
    id="close-duplicates"
    class="mt-2 w-full"
    size="small"
    severity="secondary"
    label="Close duplicate tabs"
    :disabled="closing"
    @click="closeDuplicates"
  />
  <div id="status" class="mt-3 min-h-[1.4em]" role="status" aria-live="polite">
    <Message v-if="status.error" severity="error" size="small">{{ status.text }}</Message>
    <p v-else class="m-0 text-muted-color">{{ status.text }}</p>
  </div>
  <ul id="groups" class="mt-2 list-none p-0" aria-label="PR tab groups">
    <li v-for="(group, index) in groups" :key="index" class="flex items-center gap-2 py-[3px]">
      <span
        class="size-2.5 flex-none rounded-full shadow-[inset_0_0_0_1px_rgb(0_0_0/0.3)] dark:shadow-none"
        :class="DOTS[group.color ?? ''] ?? DOTS.grey"
        aria-hidden="true"
      />
      <span>{{ group.title }}</span>
      <Badge
        class="ml-auto tabular-nums"
        severity="secondary"
        :value="group.count"
        :aria-label="`${group.count} PR${group.count === 1 ? '' : 's'}`"
      />
    </li>
  </ul>
</template>
