import { useAsyncState } from "@vueuse/core";
import { computed, ref, toValue, type MaybeRefOrGetter } from "vue";
import { isBareNumber } from "../core";
import { failed, type RepoChoices } from "../messages";
import type { Ask } from "./useServiceWorker";

export type RepoPicker = ReturnType<typeof useRepoPicker>;

// The repo dropdown: available with two or more listed repos, visible while the input is a bare PR number.
export function useRepoPicker(ask: Ask, input: MaybeRefOrGetter<string>) {
  const repo = ref("");
  async function loadChoices(): Promise<RepoChoices | null> {
    const reply = await ask({ type: "repos" });
    if (failed(reply) || !reply.show) {
      return null;
    }
    return reply;
  }
  function handleChoicesLoaded(loaded: RepoChoices | null) {
    if (loaded) {
      repo.value = loaded.selected;
    }
  }
  const { state: choices, executeImmediate } = useAsyncState(loadChoices, null, {
    immediate: false,
    onSuccess: handleChoicesLoaded,
  });
  const repos = computed(() => [...(choices.value?.repos ?? [])]);
  const available = computed(() => choices.value !== null);
  const visible = computed(() => available.value && isBareNumber(toValue(input)));
  async function load() {
    await executeImmediate();
  }
  function pick() {
    void ask({ type: "pickRepo", repo: repo.value });
  }
  return { repos, repo, available, visible, load, pick };
}
