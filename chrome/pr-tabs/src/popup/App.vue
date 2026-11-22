<script setup lang="ts">
import { computed, onMounted, ref, useTemplateRef } from "vue";
import { bareNumber, isPrUrl } from "#/core";
import { getPluralMessage } from "#/i18n";
import type { GroupListControl, PrKey, TabId } from "#/types";
import { useFuzzyFilter } from "#/composables/useFuzzyFilter";
import { usePrClose } from "#/composables/usePrClose";
import { useOpeningProgress } from "#/composables/useOpeningProgress";
import { useMessages } from "#/composables/useMessages";
import { usePanes } from "#/composables/usePanes";
import { useGoTo } from "#/composables/useGoTo";
import { useRepoPicker } from "#/composables/useRepoPicker";
import { useServiceWorker } from "#/composables/useServiceWorker";
import { useStatus } from "#/composables/useStatus";
import { useSummary } from "#/composables/useSummary";
import { useTabActions } from "#/composables/useTabActions";
import ActionMenu from "./components/ActionMenu.vue";
import GroupList from "./components/GroupList.vue";
import OpeningProgress from "./components/OpeningProgress.vue";
import PopupField from "./components/PopupField.vue";
import StatusLine from "./components/StatusLine.vue";

const {
  openReviewsButton,
  openReviewsHint,
  openAllButton,
  openAllHint,
  checkNowButton,
  checkNowHint,
  closeDuplicatesButton,
  closeDuplicatesHint,
  actionMenuLabel,
} = useMessages([
  "openReviewsButton",
  "openReviewsHint",
  "openAllButton",
  "openAllHint",
  "checkNowButton",
  "checkNowHint",
  "closeDuplicatesButton",
  "closeDuplicatesHint",
  "actionMenuLabel",
]);
const { ask } = useServiceWorker();
const status = useStatus();
const { statusText, isStatusError } = status;
const { groups, refresh } = useSummary(ask, status);
const hasPrTabs = computed(() => groups.value.length > 0);
const panes = usePanes(ask, status);
const input = ref("");
const mode = computed<"idle" | "goto" | "search">(() => {
  const value = input.value.trim();

  if (!value) {
    return "idle";
  }

  return bareNumber(value) || isPrUrl(value) ? "goto" : "search";
});
const query = computed(() => (mode.value === "search" ? input.value : ""));
const {
  groups: visibleGroups,
  active: isFiltering,
  count: matchCount,
  best: bestMatch,
} = useFuzzyFilter(groups, query);
const groupList = useTemplateRef<GroupListControl>("groupList");
const closing = usePrClose({ ask, status, refresh });
const picker = useRepoPicker(ask, input);
const { repos, repo, guessUnavailable } = picker;
const isRepoPickerOpen = ref(false);
const { busy: isGoToBusy, go, focusPr } = useGoTo({ ask, status, picker, refresh }, input);
const { openAll, openReviews, checkNow, closeDuplicates } = useTabActions({ ask, status, refresh });
const { busy: isOpeningAll } = openAll;
const { busy: isOpeningReviews } = openReviews;
const { busy: isCheckingNow } = checkNow;
const { busy: isClosingDuplicates } = closeDuplicates;
const localBusy = computed(() => isOpeningAll.value || isOpeningReviews.value);
const workerProgress = useOpeningProgress({ ask, status, refresh, localBusy });
const openingProgress = computed(() => ({
  ...workerProgress.value,
  active: workerProgress.value.active || localBusy.value,
}));
const isOpeningBatch = computed(() => openingProgress.value.active);
const totalPrs = computed(() => groups.value.reduce((total, group) => total + group.prs.length, 0));
const footerSummary = computed(() =>
  isFiltering.value
    ? `${matchCount.value} of ${totalPrs.value} PRs`
    : `${getPluralMessage("prCount", totalPrs.value)} in ${getPluralMessage("groupCount", groups.value.length)}`,
);
const footerHint = computed(() =>
  mode.value === "goto" ? "↵ open · ↑↓ repository · esc close" : "↑↓ select · ↵ open · esc clear",
);

function openingActionMenuItems() {
  return [
    {
      id: "open-all",
      label: openAllButton,
      hint: openAllHint,
      icon: "open" as const,
      busy: isOpeningBatch.value,
      run: handleOpenAllRun,
    },
    {
      id: "open-reviews",
      label: openReviewsButton,
      hint: openReviewsHint,
      icon: "review" as const,
      busy: isOpeningBatch.value,
      run: handleOpenReviewsRun,
    },
  ];
}

function closeActionMenuItems() {
  return [
    {
      id: "check-now",
      label: checkNowButton,
      hint: checkNowHint,
      icon: "merge" as const,
      busy: isCheckingNow.value || !hasPrTabs.value,
      run: handleCheckNowRun,
    },
    {
      id: "close-duplicates",
      label: closeDuplicatesButton,
      hint: closeDuplicatesHint,
      icon: "copy" as const,
      busy: isClosingDuplicates.value || !hasPrTabs.value,
      run: handleCloseDuplicatesRun,
    },
  ];
}

const actionMenuItems = computed(() => [...openingActionMenuItems(), ...closeActionMenuItems()]);

function handleSearchNext(): void {
  groupList.value?.focusFirst();
}

function handlePrClose(key: PrKey, tabId: TabId): void {
  void closing.close(key, tabId);
}

function handlePrGo(key: PrKey): void {
  void focusPr(key);
}

function handleGoToSubmit(): void {
  if (bareNumber(input.value) && !isRepoPickerOpen.value) {
    picker.suggest();
    isRepoPickerOpen.value = true;

    return;
  }

  void go();
}

function handleFieldClear(): void {
  if (isRepoPickerOpen.value) {
    isRepoPickerOpen.value = false;

    return;
  }

  input.value = "";
}

function handleRepoPick(): void {
  picker.pick();
}

function handleOpenAllRun(): void {
  void openAll.run();
}

function handleOpenReviewsRun(): void {
  void openReviews.run();
}

function handleCheckNowRun(): void {
  void checkNow.run();
}

function handleCloseDuplicatesRun(): void {
  void closeDuplicates.run();
}

function handlePopupMounted(): void {
  void refresh();
  void picker.load();
}

onMounted(handlePopupMounted);
</script>

<template>
  <main id="popup-content">
    <header class="flex h-7 items-center justify-between px-3 pt-3">
      <h1 class="text-[13px] leading-5 font-semibold">Go to a PR</h1>
      <ActionMenu :actions="actionMenuItems" :label="actionMenuLabel" />
    </header>
    <PopupField
      v-model="input"
      :mode="mode"
      :matches="matchCount"
      :busy="isGoToBusy"
      @go="handleGoToSubmit"
      @next="handleSearchNext"
      @clear="handleFieldClear"
    />
    <OpeningProgress :progress="openingProgress" />
    <StatusLine v-if="isStatusError" :text="statusText" error />
    <section
      v-if="isRepoPickerOpen && mode === 'goto' && repos.length"
      class="px-3 pt-3"
      aria-label="Repository suggestions"
    >
      <p class="pl-2 text-[12px] leading-4 text-[var(--muted)]">Open #{{ bareNumber(input) }} in</p>
      <div class="mt-1" role="listbox">
        <button
          v-for="name in repos"
          :key="name"
          type="button"
          class="flex h-8 w-full items-center gap-2 rounded-md px-3 text-left hover:bg-[var(--hover)]"
          :class="name === repo ? 'bg-[var(--sel)]' : ''"
          role="option"
          :aria-selected="name === repo"
          @click="
            repo = name;
            handleRepoPick();
          "
        >
          <span class="h-4 w-[3px] rounded-sm bg-[var(--pr-tabs-group-blue)]" />
          <span class="font-semibold">{{ name }}</span>
          <span class="ml-auto text-[12px] text-[var(--muted)]">↵</span>
        </button>
      </div>
      <small v-if="guessUnavailable" class="mt-1 block text-[var(--muted)]">
        repo guess unavailable: update the host
      </small>
    </section>
    <section v-if="hasPrTabs" id="popup-list" class="min-h-0 flex-1 overflow-y-auto px-2 pt-3 pb-2">
      <p
        v-if="isRepoPickerOpen && mode === 'goto'"
        class="px-3 pt-4 pb-1 text-[12px] leading-4 text-[var(--muted)]"
      >
        Open tabs matching {{ bareNumber(input) }}
      </p>
      <GroupList
        ref="groupList"
        :groups="visibleGroups"
        :panes="panes"
        :filtering="isFiltering"
        @go="handlePrGo"
        @close="handlePrClose"
      />
    </section>
    <section v-else class="px-3 py-6">
      <p class="font-medium">No PR tabs open</p>
      <p class="mt-1 max-w-[360px] text-[12px] leading-[18px] text-[var(--muted)]">
        Type a PR number like 2972 or paste a PR link. Enter picks the repository, Enter again opens
        it.
      </p>
    </section>
    <footer
      v-if="hasPrTabs"
      class="flex h-8 shrink-0 items-center justify-between border-t border-[var(--line)] px-3 text-[12px] leading-4 text-[var(--muted)]"
    >
      <span>{{ footerSummary }}</span>
      <span>{{ footerHint }}</span>
    </footer>
  </main>
</template>
