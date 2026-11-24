import { computed, onMounted, onUnmounted, ref, type Ref } from "vue";
import { loadFileSettings, saveFileSettings, watchFileSettings } from "#/files/settings";
import type { FileSettings } from "#/files/settings";

export function useFileSettings() {
  const settings = ref<FileSettings>({ enabled: false, patterns: [] });
  const pattern = ref("");
  const ready = ref(false);
  const failed = ref(false);
  const disabled = computed(() => !ready.value);
  const source = crypto.randomUUID();
  const persist = settingsWriter(settings, failed, source);

  function handleToggle(event: Event): void {
    const enabled = (event.target as HTMLInputElement).checked;
    void persist({ ...settings.value, enabled });
  }

  function handleAdd(): void {
    const value = pattern.value.trim();
    if (value && !settings.value.patterns.includes(value)) {
      void persist({ ...settings.value, patterns: [...settings.value.patterns, value] });
      pattern.value = "";
    }
  }

  function remove(value: string): void {
    void persist({
      ...settings.value,
      patterns: settings.value.patterns.filter((item) => item !== value),
    });
  }

  bindSettings({ settings, ready, failed, source });
  const enabled = computed(() => settings.value.enabled);
  const patterns = computed(() => settings.value.patterns);

  return { enabled, patterns, pattern, disabled, failed, handleToggle, handleAdd, remove };
}

interface SettingsRefs {
  settings: Ref<FileSettings>;
  ready: Ref<boolean>;
  failed: Ref<boolean>;
  source: string;
}

function bindSettings(state: SettingsRefs): void {
  let stop: (() => void) | undefined;
  onMounted(async () => {
    try {
      state.settings.value = await loadFileSettings();
      stop = watchFileSettings((value) => {
        state.settings.value = value;
      }, state.source);
      state.ready.value = true;
    } catch {
      state.failed.value = true;
    }
  });
  onUnmounted(() => stop?.());
}

function settingsWriter(settings: Ref<FileSettings>, failed: Ref<boolean>, source: string) {
  let writes = Promise.resolve();

  return (next: FileSettings): void => {
    settings.value = next;
    failed.value = false;
    writes = writes
      .then(() => saveFileSettings(next, source))
      .catch(() => {
        failed.value = true;
      });
  };
}
