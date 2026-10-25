import { getPlainMessage } from "#/i18n";
import type { Labels, PlainMessageKey } from "#/types";

// The text of each key, for templates that bind labels as plain variables.
export function useMessages<K extends PlainMessageKey>(keys: readonly K[]): Labels<K> {
  const labels: Partial<Record<K, string>> = {};

  for (const key of keys) {
    labels[key] = getPlainMessage(key);
  }

  return labels as Labels<K>;
}
