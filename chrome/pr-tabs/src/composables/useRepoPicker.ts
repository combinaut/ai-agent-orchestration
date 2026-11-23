import { useAsyncState } from "@vueuse/core";
import { computed, ref, toValue, type MaybeRefOrGetter } from "vue";
import { bareNumber, hasBareNumber, suggestedRepo } from "#/core";
import { failed } from "#/messages";
import type { Ask, RepoChoices, RepoName, RepoPicker } from "#/types";

function noop(): void {}

// load starts a fetch; ready waits for the current one, starting it when none has run.
function loadOnce(execute: () => Promise<unknown>): Pick<RepoPicker, "load" | "ready"> {
  let loading: Promise<void> | undefined;

  async function load(): Promise<void> {
    loading = execute().then(noop);
    await loading;
  }

  async function ready(): Promise<void> {
    await (loading ?? load());
  }

  return { load, ready };
}

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
      suggest();
    }
  }

  const { state: choices, executeImmediate } = useAsyncState(() => loadChoices(ask), null, {
    immediate: false,
    onSuccess: handleChoicesLoaded,
  });

  const repos = computed(() => [...(choices.value?.repos ?? [])]);
  const available = computed(() => choices.value !== null);
  const visible = computed(() => available.value && hasBareNumber(toValue(input)));
  const guessUnavailable = computed(() => choices.value?.guessUnavailable ?? false);

  function suggest(value = toValue(input)): void {
    const selected = preselectedRepo(choices.value, value);

    if (selected) {
      repo.value = selected;
    }
  }

  const { load, ready } = loadOnce(executeImmediate);

  function pick(): void {
    void ask({ type: "pickRepo", repo: repo.value });
  }

  return { repos, repo, available, visible, guessUnavailable, load, ready, suggest, pick };
}
