import { computed, onMounted, ref, watch, type Ref } from "vue";
import { useServiceWorker } from "#/composables/useServiceWorker";
import { getMessage } from "#/i18n";
import { failed } from "#/messages";
import { validRepository } from "#/repositories";
import type { Ask, RepositorySettings } from "#/types";

interface SettingsState {
  settings: Ref<RepositorySettings>;
  repository: Ref<string>;
  feedback: Ref<string>;
  busy: Ref<boolean>;
}

function bindSettings(state: SettingsState, ask: Ask): void {
  onMounted(async () => {
    const reply = await ask({ type: "repositorySettings" });
    if (failed(reply)) {
      state.feedback.value = reply.error;
    } else {
      state.settings.value = reply;
      state.busy.value = false;
    }
  });
  watch(state.repository, () => {
    state.feedback.value = "";
  });
}

function settingsWriter(state: SettingsState, ask: Ask) {
  return async (repo: string, removing: boolean): Promise<void> => {
    state.busy.value = true;
    try {
      const reply = await ask({ type: removing ? "removeRepository" : "addRepository", repo });
      if (failed(reply)) {
        state.feedback.value = reply.error;

        return;
      }
      state.feedback.value = "";
      state.settings.value = reply;
      if (!removing) {
        state.repository.value = "";
      }
    } finally {
      state.busy.value = false;
    }
  };
}

function inputIssue(value: string, settings: RepositorySettings): string {
  if (!validRepository(value)) {
    return getMessage("repositoryInvalid");
  }

  return [...settings.configured, ...settings.added].includes(value)
    ? getMessage("repositoryDuplicate")
    : "";
}

function inputActions(state: SettingsState, change: ReturnType<typeof settingsWriter>) {
  function handleAdd(): void {
    if (state.busy.value) {
      return;
    }
    const value = state.repository.value.trim().toLowerCase();
    state.feedback.value = inputIssue(value, state.settings.value);
    if (!state.feedback.value) {
      void change(value, false);
    }
  }
  function remove(repo: string): void {
    if (!state.busy.value) {
      void change(repo, true);
    }
  }

  return { handleAdd, remove };
}

export function useRepositorySettings(ask: Ask = useServiceWorker().ask) {
  const settings = ref<RepositorySettings>({ configured: [], added: [] });
  const repository = ref("");
  const feedback = ref("");
  const busy = ref(true);
  const state = { settings, repository, feedback, busy };
  const entries = computed(() =>
    Object.fromEntries(
      [
        ...settings.value.configured.map((repo) => ({ repo, configured: true })),
        ...settings.value.added
          .filter((repo) => !settings.value.configured.includes(repo))
          .map((repo) => ({ repo, configured: false })),
      ].map((entry) => [entry.repo, entry]),
    ),
  );
  bindSettings(state, ask);
  const actions = inputActions(state, settingsWriter(state, ask));

  return { entries, repository, feedback, busy, ...actions };
}
