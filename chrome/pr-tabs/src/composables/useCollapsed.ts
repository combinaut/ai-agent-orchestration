import { ref } from "vue";
import type { Collapsed, StoredState } from "#/types";

const KEY = "prTabsCollapsed";

export function useCollapsed(): Collapsed {
  const collapsed = ref<ReadonlySet<string>>(new Set());

  async function load(): Promise<void> {
    const stored = await chrome.storage.local.get<Pick<StoredState, typeof KEY>>(KEY);

    collapsed.value = new Set(stored[KEY] ?? []);
  }

  function set(id: string, isCollapsed: boolean): void {
    const next = new Set(collapsed.value);

    if (isCollapsed) {
      next.add(id);
    } else {
      next.delete(id);
    }

    collapsed.value = next;
    void chrome.storage.local.set({ [KEY]: [...next] });
  }

  return { collapsed, load, set };
}
