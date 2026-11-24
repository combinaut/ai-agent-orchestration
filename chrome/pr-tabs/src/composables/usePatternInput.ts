import { computed, ref, watch } from "vue";
import type { Ref } from "vue";
import { getMessage } from "#/i18n";

type PatternIssue = "filePatternEmpty" | "filePatternDuplicate";

export function usePatternInput(patterns: Ref<string[]>, add: (value: string) => void) {
  const pattern = ref("");
  const issue = ref<PatternIssue | null>(null);
  const feedback = computed(() => (issue.value ? getMessage(issue.value) : ""));
  const invalid = computed(() => issue.value !== null);

  function clearIssue(): void {
    issue.value = null;
  }

  function handleAdd(): void {
    const value = pattern.value.trim();
    issue.value = patternIssue(value, patterns.value);
    if (issue.value) {
      return;
    }
    add(value);
    pattern.value = "";
  }

  watch(pattern, clearIssue);

  return { pattern, feedback, invalid, handleAdd };
}

function patternIssue(value: string, patterns: string[]): PatternIssue | null {
  if (!value) {
    return "filePatternEmpty";
  }

  return patterns.includes(value) ? "filePatternDuplicate" : null;
}
