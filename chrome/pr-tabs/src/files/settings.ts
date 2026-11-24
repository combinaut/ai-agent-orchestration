export interface FileSettings {
  enabled: boolean;
  patterns: string[];
}

export const FILE_SETTINGS_KEY = "hiddenFileSettings";
export const DEFAULT_PATTERNS = ["*.stories.*", "*_spec.rb", "*.spec.ts"];

export function parseFileSettings(value: unknown): FileSettings {
  if (typeof value !== "object" || value === null) {
    return { enabled: false, patterns: [...DEFAULT_PATTERNS] };
  }

  const enabled = "enabled" in value && value.enabled === true;
  const patterns = "patterns" in value ? value.patterns : null;
  const valid = Array.isArray(patterns) && patterns.every((item) => typeof item === "string");

  return { enabled, patterns: valid ? patterns : [...DEFAULT_PATTERNS] };
}

export async function loadFileSettings(): Promise<FileSettings> {
  const stored = await chrome.storage.sync.get(FILE_SETTINGS_KEY);

  return parseFileSettings(stored[FILE_SETTINGS_KEY]);
}

export async function saveFileSettings(settings: FileSettings, source?: string): Promise<void> {
  const value = source ? { ...settings, source } : settings;
  await chrome.storage.sync.set({ [FILE_SETTINGS_KEY]: value });
}

export function watchFileSettings(
  apply: (settings: FileSettings) => void,
  ignoredSource?: string,
): () => void {
  function changed(changes: Record<string, chrome.storage.StorageChange>, area: string): void {
    if (area === "sync" && FILE_SETTINGS_KEY in changes) {
      const value = changes[FILE_SETTINGS_KEY]?.newValue;
      if (!isOwnChange(value, ignoredSource)) {
        apply(parseFileSettings(value));
      }
    }
  }

  chrome.storage.onChanged.addListener(changed);

  return () => chrome.storage.onChanged.removeListener(changed);
}

function isOwnChange(value: unknown, source?: string): boolean {
  return (
    source !== undefined &&
    typeof value === "object" &&
    value !== null &&
    "source" in value &&
    value.source === source
  );
}
