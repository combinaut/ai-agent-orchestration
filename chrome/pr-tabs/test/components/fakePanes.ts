import { ref } from "vue";
import type { BranchRef, PaneLookup } from "#/types";
import type { FakePanes } from "#test/types";

// Panes with a fixed answer that record what the components ask for.
export function fakePanes(answer: PaneLookup = {}, isChecking = false): FakePanes {
  const ensured: (readonly BranchRef[])[] = [];
  const focused: BranchRef[] = [];

  async function ensure(heads: readonly BranchRef[]): Promise<void> {
    ensured.push(heads);
  }

  async function focus(head: BranchRef): Promise<void> {
    focused.push(head);
  }

  return { checking: ref(isChecking), lookup: ref(answer), ensure, focus, ensured, focused };
}
