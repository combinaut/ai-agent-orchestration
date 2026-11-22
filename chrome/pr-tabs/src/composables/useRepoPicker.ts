import { useAsyncState } from "@vueuse/core";
import { computed, ref, toValue, watch, type MaybeRefOrGetter } from "vue";
import { bareNumber, hasBareNumber, suggestedRepo } from "#/core";
import { failed } from "#/messages";
import type { Ask, RepoChoices, RepoName, RepoPicker } from "#/types";

function preselectedRepo(choices: RepoChoices | null, input: string): RepoName | null {
  const number = bareNumber(input);

  return number && choices
    ? suggestedRepo(choices.repos, number, { fallback: choices.selected, newest: choices.newest })
    : null;
}

async function loadChoices(ask: Ask): Promise<RepoChoices | null> {
  const reply = await ask({ type: "repos" });

  return failed(reply) || !reply.show ? null : reply;
}

export function useRepoPicker(ask: Ask, input: MaybeRefOrGetter<string>): RepoPicker {
  const repo = ref<RepoName>("");

  function handleChoicesLoaded(loaded: RepoChoices | null): void {
    if (loaded) {
      repo.value = loaded.selected;
      preselectRepo();
    }
  }

  const { state: choices, executeImmediate } = useAsyncState(() => loadChoices(ask), null, {
    immediate: false,
    onSuccess: handleChoicesLoaded,
  });

  const repos = computed(() => [...(choices.value?.repos ?? [])]);
  const available = computed(() => choices.value !== null);
  const visible = computed(() => available.value && hasBareNumber(toValue(input)));

  function preselectRepo(value = toValue(input)): void {
    const selected = preselectedRepo(choices.value, value);

    if (selected) {
      repo.value = selected;
    }
  }

  watch(() => toValue(input), preselectRepo, { flush: "sync" });

  async function load(): Promise<void> {
    await executeImmediate();
  }

  function pick(): void {
    void ask({ type: "pickRepo", repo: repo.value });
  }

  return { repos, repo, available, visible, load, pick };
}
