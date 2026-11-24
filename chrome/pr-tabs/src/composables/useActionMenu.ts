import { ref } from "vue";

export function useActionMenu() {
  const open = ref(false);

  function toggle(): void {
    open.value = !open.value;
  }

  function close(): void {
    open.value = false;
  }

  return { open, toggle, close };
}
