import { useAsyncState } from "@vueuse/core";
import { computed, ref, toValue, type MaybeRefOrGetter } from "vue";
import { hasBareNumber } from "#/core";
import { failed } from "#/messages";
import type { Ask, RepoChoices, RepoName, RepoPicker } from "#/types";

export function useRepoPicker(ask: Ask, input: MaybeRefOrGetter<string>): RepoPicker {
  const repo = ref<RepoName>("");

  async function loadChoices(): Promise<RepoChoices | null> {
    const reply = await ask({ type: "repos" });

    if (failed(reply) || !reply.show) {
      return null;
    }

    return reply;
  }

  function handleChoicesLoaded(loaded: RepoChoices | null): void {
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
  const visible = computed(() => available.value && hasBareNumber(toValue(input)));

  async function load(): Promise<void> {
    await executeImmediate();
  }

  function pick(): void {
    void ask({ type: "pickRepo", repo: repo.value });
  }

  return { repos, repo, available, visible, load, pick };
}
